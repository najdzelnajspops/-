"""Веб-UI (FastAPI) — второй способ запуска сервиса, РЯДОМ с CLI (pipeline/run.py,
pipeline/review_run.py), не вместо него (см. docs/TASK_BRIEF.md — CLI остаётся
основным способом по ТЗ, UI — плюс). Прямой запрос пользователя, 2026-09-18:
живой веб-сервис, оба режима работы (автогенерация + проверка выбора
пользователя), карта/реестр зданий data.mos.ru — отдельным шагом позже, не здесь.

Запуск: `uvicorn pipeline.web.app:app --reload` (dev) или без --reload в проде.
Swagger/OpenAPI — на /docs "из коробки" (отдельный критерий оценки ТЗ).

Работает на заранее подготовленных демо-объектах (pipeline/web/demo_objects.py)
и, с 2026-09-19 (прямой запрос пользователя — «а если на презентации дадут
новый DWG?»), на объектах, загруженных прямо через UI (pipeline/web/uploads.py)
— синхронно, со спиннером на фронте: обработка крупного объекта (десятки
гектаров) может занять несколько минут, это честно показано пользователю,
не скрыто иллюзией мгновенного ответа.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.catalog.planting_cost import DEFAULT_PATH as PLANTING_COST_PATH
from pipeline.catalog.planting_cost import PlantingCostCatalog
from pipeline.catalog.price_list_ingest import (
    CategoryProposal,
    UnsupportedPriceListFormatError,
    build_proposal,
    classify_row,
    parse_price_list,
)
from pipeline.catalog.species_territory_recommendations import SpeciesTerritoryRegistry
from pipeline.constraints.buffer_engine import build_constraint_map
from pipeline.constraints.existing_vegetation import ExistingVegetationPolicy
from pipeline.constraints.lep_voltage_override import apply_lep_voltage_override
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.constraints.red_lines_policy import apply_red_lines_policy
from pipeline.ingest.dwg_convert import ConversionFailedError, ConverterNotFoundError
from pipeline.ingest.dxf_ingest import ingest_dxf_files
from pipeline.ingest.layer_classifier import LayerClassifier
from pipeline.placement.density import PlantingDensityRegistry
from pipeline.report.human_report import render_human_report
from pipeline.review.placement_review import ProposedPlanting, review_human_placements
from pipeline.run import run_service_a
from pipeline.web import results as result_store
from pipeline.web.audit_log import log_event
from pipeline.web.demo_objects import get_demo_object, list_demo_objects, resolve_base_dxf, resolve_input_paths
from pipeline.web.geometry_utils import geometry_bounds, polygon_to_rings
from pipeline.web.uploads import (
    UploadValidationError,
    forget_upload,
    get_uploaded_object,
    list_uploaded_objects,
    register_upload,
)

app = FastAPI(
    title="Проект озеленения территории — веб-UI",
    description=(
        "Второй способ запуска сервиса (рядом с CLI): визуализация плана + "
        "проверка предложенной пользователем посадки (Placement Review Agent)."
    ),
    version="0.1.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

_TERRITORY_CATEGORIES = [
    "dvorovye", "doshkolnye", "obscheobr", "zdravoohr",
    "magistrali", "ploschadi", "parki", "proizvodstvennye",
]


def _require_territory_category(value: str | None) -> None:
    """Неизвестная категория — ошибка запроса (422 с перечнем допустимых), а не 500."""
    if value is not None and value not in _TERRITORY_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестная категория территории {value!r}. Допустимые значения: {', '.join(_TERRITORY_CATEGORIES)}.",
        )


class ProposalIn(BaseModel):
    id: str
    species_name_ru: str
    life_form: Literal["tree", "shrub"]
    x: float
    y: float


class ReviewRequest(BaseModel):
    territory_category: str
    proposals: list[ProposalIn]
    # Ручное указание класса напряжения ЛЭП (2026-09-25) — см. GenerateRequest ниже.
    lep_voltage_kv: float | None = None


class GenerateRequest(BaseModel):
    territory_category: str
    # Ручное указание класса напряжения ЛЭП пользователем (2026-09-25, прямой
    # запрос пользователя — «пользователь имеет дополнительные материалы на руках и
    # может самостоятельно определить класс объекта»): если задан, ВСЕ ЛЭП с
    # неопределённым по чертежу классом напряжения на этом участке получают
    # отступ по этому классу (ПП РФ №160) вместо максимально консервативного
    # (55 м) по умолчанию. None — поведение не меняется.
    lep_voltage_kv: float | None = None
    # Чек-боксы видов на вкладке автогенерации (2026-09-24) — {"tree": [...],
    # "shrub": [...]}, ключ отсутствует/None = прежнее однвидовое поведение по
    # умолчанию для этой формы (см. pipeline/placement/generator.py,
    # generate_placement(selected_species_names=...)). Необязательное поле —
    # старые запросы без него (текущие тесты) продолжают работать как раньше.
    selected_species: dict[str, list[str]] | None = None


class _ResolvedObject(BaseModel):
    label_ru: str
    default_territory_category: str
    input_paths: list[Path]
    base_dxf_path: Path


def _resolve_object(object_id: str) -> _ResolvedObject:
    """Находит объект и по демо-реестру (pipeline/web/demo_objects.py), и по
    реестру загруженных через UI объектов (pipeline/web/uploads.py) — с
    2026-09-19 (прямой запрос пользователя) оба источника равноправны для
    geometry/generate/review, разница только в происхождении файлов."""
    uploaded = get_uploaded_object(object_id)
    if uploaded is not None:
        return _ResolvedObject(
            label_ru=uploaded.label_ru,
            default_territory_category=uploaded.default_territory_category,
            input_paths=list(uploaded.input_paths),
            base_dxf_path=uploaded.base_dxf_path,
        )
    try:
        obj = get_demo_object(object_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _ResolvedObject(
        label_ru=obj.label_ru,
        default_territory_category=obj.default_territory_category,
        input_paths=resolve_input_paths(obj),
        base_dxf_path=resolve_base_dxf(obj),
    )


def _prepared_site(object_id: str, lep_voltage_kv: float | None = None):
    resolved = _resolve_object(object_id)

    ingest_result = ingest_dxf_files(resolved.input_paths)
    if ingest_result.site_boundary is None:
        raise HTTPException(
            status_code=500,
            detail=f"Граница участка не найдена для объекта {object_id!r} — проверьте состав загруженных файлов "
            "(нужен слой границы участка) либо это баг демо-данных, если объект не был загружен пользователем.",
        )
    veg_policy = ExistingVegetationPolicy()
    features = veg_policy.apply_to_features(ingest_result.features)
    features = apply_red_lines_policy(features)
    # Ручное указание класса напряжения ЛЭП пользователем (2026-09-25, прямой
    # запрос пользователя) — см. pipeline/constraints/lep_voltage_override.py.
    # None (по умолчанию) не меняет поведение.
    features = apply_lep_voltage_override(features, lep_voltage_kv, LepZoneRegistry())
    return resolved, ingest_result, features


def _unrecognized_layers_hint(input_paths) -> str:
    """Короткая подсказка к ошибке «граница не найдена»: какие слои файла сервис не распознал.
    Разбор уже сделан и лежит в кэше, поэтому повторный вызов быстрый; любой сбой не мешает ответу."""
    try:
        result = ingest_dxf_files(input_paths)
        hits = sorted(result.unclassified_layers.values(), key=lambda h: -h.entity_count)[:6]
    except Exception:
        return ""
    if not hits:
        return ""
    names = ", ".join(f"«{h.category}»" for h in hits)
    return (
        f" Сервис читает слои геодезической подосновы («Граница площадки», «Здания», «Водопровод» и т. п.); "
        f"в вашем файле крупнейшие нераспознанные слои: {names}. Если это проектный чертёж, а не подоснова, "
        "подайте вместе с ним файл подосновы с границей участка."
    )


@app.get("/api/demo-objects")
def api_list_demo_objects():
    demo_entries = [
        {
            "id": obj.id,
            "label_ru": obj.label_ru,
            "default_territory_category": obj.default_territory_category,
        }
        for obj in list_demo_objects()
    ]
    # Загруженные через UI объекты (2026-09-19) — в том же списке, чтобы сразу
    # появлялись в выпадающем меню объекта наравне с демо-объектами.
    uploaded_entries = [
        {
            "id": obj.id,
            "label_ru": obj.label_ru,
            "default_territory_category": obj.default_territory_category,
        }
        for obj in list_uploaded_objects()
    ]
    return demo_entries + uploaded_entries


@app.post("/api/uploads")
def api_upload(files: list[UploadFile] = File(...), label_ru: str | None = Form(None)):
    """Загрузка произвольного DWG/DXF через веб-UI — прямой ответ на вопрос
    пользователя (2026-09-19): «а если на презентации дадут новый DWG файл?».
    Обрабатывается синхронно (см. докстринг модуля) — на крупных объектах
    может занять несколько минут, фронт обязан честно показать спиннер."""
    try:
        obj = register_upload(files, label_ru)
    except UploadValidationError as exc:
        log_event("upload", "-", "-", {"filenames": [f.filename for f in files]}, {"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConverterNotFoundError as exc:
        log_event("upload", "-", "-", {"filenames": [f.filename for f in files]}, {"error": str(exc)})
        raise HTTPException(
            status_code=503,
            detail=f"На сервере не найден ODA File Converter — загрузка DWG недоступна. "
            f"Попробуйте загрузить готовый DXF вместо DWG. ({exc})",
        ) from exc
    except ConversionFailedError as exc:
        log_event("upload", "-", "-", {"filenames": [f.filename for f in files]}, {"error": str(exc)})
        raise HTTPException(status_code=422, detail=f"Не удалось сконвертировать DWG в DXF: {exc}") from exc

    # Сразу проверяем, что из загруженных файлов вообще извлекается граница
    # участка — честная быстрая обратная связь, а не ждать первого клика по
    # «Сгенерировать план», чтобы узнать, что файлы не подходят.
    # Это проблема загруженного файла, а не сбой сервера: отвечаем 422 с понятным текстом
    # (раньше битый DXF давал голый 500, а негодный объект оставался в списке) и
    # убираем объект из реестра.
    try:
        _prepared_site(obj.id)
    except HTTPException as exc:
        forget_upload(obj.id)
        detail = (
            "Граница участка в загруженных файлах не найдена — нужен слой границы участка "
            "(например «Граница заказа» или «Граница работ»). Проверьте состав файлов."
            + _unrecognized_layers_hint(obj.input_paths)
        )
        log_event("upload", obj.id, "-", {"filenames": [f.filename for f in files]}, {"error": exc.detail})
        raise HTTPException(status_code=422, detail=detail) from exc
    except Exception as exc:
        forget_upload(obj.id)
        # Текст исключения — только в журнал: в нём служебные пути и английский текст библиотеки.
        log_event("upload", obj.id, "-", {"filenames": [f.filename for f in files]}, {"error": f"{type(exc).__name__}: {exc}"})
        raise HTTPException(
            status_code=422,
            detail="Не удалось прочитать загруженные файлы как DXF-чертёж. Проверьте, что файл не повреждён "
            "и это настоящий DXF (или DWG).",
        ) from exc

    log_event(
        "upload",
        obj.id,
        obj.default_territory_category,
        {"filenames": [f.filename for f in files], "label_ru": label_ru},
        {"id": obj.id, "label_ru": obj.label_ru},
    )
    return {
        "id": obj.id,
        "label_ru": obj.label_ru,
        "default_territory_category": obj.default_territory_category,
    }


@app.get("/api/territory-categories")
def api_territory_categories():
    catalog = DpioosSpeciesCatalog()
    return [{"id": tc, "label_ru": catalog.territory_categories.get(tc, tc)} for tc in _TERRITORY_CATEGORIES]


@app.get("/api/data-sources")
def api_data_sources():
    """Прямой ответ на запрос пользователя (2026-09-19) об обновлении
    нормативной базы: справочники обновляются вручную (заменой файла в
    data/reference/, см. docs/DATA_UPDATE_GUIDE.md) — этот эндпоинт честно
    показывает, какая версия (дата изменения файла на диске) каждого
    справочника сейчас реально используется сервисом."""
    registries = [
        ("Каталог видов ДПиООС (743-ПП/369-ПП, категории территорий)", DpioosSpeciesCatalog()),
        ("Виды по категориям территорий, 623-ПП Табл. В.6 (вторичная сверка)", SpeciesTerritoryRegistry()),
        ("Инвазивные виды, 369-ПП", InvasiveSpeciesRegistry()),
        ("Отступы от коммуникаций, 743-ПП Табл. 3.6.1", OffsetRegistry()),
        ("Охранные зоны ЛЭП, ПП РФ №160", LepZoneRegistry()),
        ("Норма густоты посадки, 623-ПП Табл. В.1", PlantingDensityRegistry()),
        ("Отступ от существующих насаждений (допущение проекта)", ExistingVegetationPolicy()),
        ("Оценочная стоимость посадочного материала (не норма)", PlantingCostCatalog()),
        ("Соответствие имён слоёв DXF (инженерная, не нормативная)", LayerClassifier()),
    ]
    return [
        {"label_ru": label, "file": registry._path.name, "updated_at": registry.source_updated_at}
        for label, registry in registries
    ]


@app.get("/api/demo-objects/{demo_id}/geometry")
def api_geometry(demo_id: str, territory_category: str | None = None, lep_voltage_kv: float | None = None):
    _require_territory_category(territory_category)
    obj, ingest_result, features = _prepared_site(demo_id, lep_voltage_kv)
    category = territory_category or obj.default_territory_category
    registry = OffsetRegistry()

    tree_map = build_constraint_map(ingest_result.site_boundary, features, "tree", registry)
    shrub_map = build_constraint_map(ingest_result.site_boundary, features, "shrub", registry)

    bounds = geometry_bounds(ingest_result.site_boundary, tree_map.allowed_zone, shrub_map.allowed_zone)
    return {
        "site_boundary_status": ingest_result.site_boundary_status,
        "territory_category": category,
        "bounds": bounds,
        "site_boundary": polygon_to_rings(ingest_result.site_boundary),
        "allowed_zone": {
            "tree": polygon_to_rings(tree_map.allowed_zone),
            "shrub": polygon_to_rings(shrub_map.allowed_zone),
        },
        # Число ЛЭП с неопределённым по чертежу классом напряжения (2026-09-25) —
        # фронт показывает это рядом с выбором класса напряжения, чтобы было
        # понятно, есть ли вообще смысл его указывать на этом объекте.
        "lep_unknown_voltage_count": len(ingest_result.lep_features_needing_voltage),
    }


@app.get("/api/lep-voltage-tiers")
def api_lep_voltage_tiers():
    """Типовые классы напряжения ЛЭП (ПП РФ №160) для ручного выбора пользователем
    (2026-09-25, прямой запрос пользователя) — см.
    pipeline/constraints/lep_voltage_override.py."""
    return LepZoneRegistry().tier_options()


@app.get("/api/species")
def api_species(territory_category: str, life_form: Literal["tree", "shrub"]):
    _require_territory_category(territory_category)
    catalog = DpioosSpeciesCatalog()
    entries = sorted(
        catalog.recommended_for(territory_category, life_form),
        key=lambda e: e.name_ru,
    )
    return [
        {
            "name_ru": e.name_ru,
            "citation": catalog.citation_for(e, territory_category),
        }
        for e in entries
    ]


@app.post("/api/demo-objects/{demo_id}/generate")
def api_generate(demo_id: str, request: GenerateRequest):
    _require_territory_category(request.territory_category)
    obj = _resolve_object(demo_id)

    # Файлы результата (DXF и отчёт для пользователя) остаются на сервере для
    # скачивания из окна сервиса — см. pipeline/web/results.py (хранятся последние
    # несколько результатов).
    result_dir = result_store.new_result_dir()
    try:
        result = run_service_a(
            input_dxf_paths=obj.input_paths,
            base_dxf_path_for_export=obj.base_dxf_path,
            territory_category=request.territory_category,
            output_dxf_path=result_dir / result_store.DXF_NAME,
            output_report_path=result_dir / "report.json",
            selected_species_names=request.selected_species,
            lep_voltage_kv_override=request.lep_voltage_kv,
        )
        # Отчёт для пользователя не должен ломать генерацию: схема или шаблон могут
        # подвести, а расчёт и DXF от этого не зависят — тогда кнопка отчёта не покажется.
        report_ready = True
        try:
            render_human_report(
                result.report,
                result.constraint_results,
                result.placement_results,
                output_dir=result_dir / result_store.REPORT_DIR_NAME,
                object_label=obj.label_ru,
            )
        except Exception:
            report_ready = False
        result_id = result_store.register_result(result_dir, demo_id)
    except BaseException:
        shutil.rmtree(result_dir, ignore_errors=True)
        raise

    response = {**result.report, "result_id": result_id, "report_available": report_ready}
    log_event("generate", demo_id, request.territory_category, request.model_dump(), response)
    return response


def _require_result(result_id: str) -> result_store.ResultEntry:
    entry = result_store.get_result(result_id)
    if entry is None or not entry.dxf_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Результат не найден: он мог устареть (хранятся последние несколько генераций) — сгенерируйте план заново.",
        )
    return entry


@app.get("/api/results/{result_id}/dxf")
def api_result_dxf(result_id: str):
    entry = _require_result(result_id)
    return FileResponse(
        entry.dxf_path,
        media_type="application/dxf",
        filename=f"zelyonyj_kontur_{entry.demo_id}.dxf",
    )


@app.get("/api/results/{result_id}/report/{filename}")
def api_result_report_file(result_id: str, filename: str):
    entry = _require_result(result_id)
    media_type = result_store.REPORT_FILES.get(filename)
    path = entry.report_dir / filename
    if media_type is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл отчёта не найден.")
    return Response(content=path.read_bytes(), media_type=media_type)


@app.post("/api/demo-objects/{demo_id}/review")
def api_review(demo_id: str, request: ReviewRequest):
    _require_territory_category(request.territory_category)
    obj, ingest_result, features = _prepared_site(demo_id, request.lep_voltage_kv)

    proposals = [
        ProposedPlanting(id=p.id, species_name_ru=p.species_name_ru, life_form=p.life_form, x=p.x, y=p.y)
        for p in request.proposals
    ]
    review = review_human_placements(ingest_result.site_boundary, features, request.territory_category, proposals)

    response = {
        "verdict": review.verdict,
        "checked_count": review.checked_count,
        "explanation": review.explanation,
        "issues": [
            {"planting_id": issue.planting_id, "kind": issue.kind, "detail": issue.detail}
            for issue in review.issues
        ],
        "total_estimated_cost_rub": review.total_estimated_cost_rub,
        "cost_unknown_count": review.cost_unknown_count,
    }
    log_event("review", demo_id, request.territory_category, request.model_dump(), response)
    return response


def _proposal_to_dict(p: CategoryProposal) -> dict:
    return {
        "category_key": p.category_key,
        "label_ru": p.label_ru,
        "is_new": p.is_new,
        "current_default_rub": p.current_default_rub,
        "proposed_default_rub": p.proposed_default_rub,
        "proposed_min_rub": p.proposed_min_rub,
        "proposed_max_rub": p.proposed_max_rub,
        "matched_row_count": p.matched_row_count,
        "sample_texts": p.sample_texts,
    }


@app.post("/api/price-list/preview")
def api_price_list_preview(file: UploadFile = File(...)):
    """Прямой запрос пользователя (2026-09-19): загрузка прайс-листа питомника
    с распознаванием категории растения. Только предпросмотр — ничего не
    пишет на диск, см. докстринг pipeline/catalog/price_list_ingest.py."""
    suffix = Path(file.filename or "").suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / f"upload{suffix}"
        tmp_path.write_bytes(file.file.read())
        try:
            rows = parse_price_list(tmp_path)
        except UnsupportedPriceListFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        return {"rows_found": 0, "proposals": []}

    catalog = DpioosSpeciesCatalog()
    classified = [classify_row(r, catalog) for r in rows]
    unclassified_count = sum(1 for c in classified if c.category_key is None)

    with open(PLANTING_COST_PATH, encoding="utf-8") as f:
        current_yaml = yaml.safe_load(f)
    proposals = build_proposal(classified, current_yaml.get("categories", {}))

    log_event(
        "price_list_preview", "-", "-",
        {"filename": file.filename},
        {"rows_found": len(rows), "unclassified_count": unclassified_count, "categories": len(proposals)},
    )
    return {
        "rows_found": len(rows),
        "unclassified_count": unclassified_count,
        "proposals": [_proposal_to_dict(p) for p in proposals],
    }


class PriceCategoryUpdate(BaseModel):
    category_key: str
    label_ru: str
    default_rub: float
    min_rub: float
    max_rub: float


class PriceListApplyRequest(BaseModel):
    updates: list[PriceCategoryUpdate]


_MAX_PRICE_BACKUPS = 2


def _backup_planting_cost_file() -> Path:
    """Бэкап перед изменением — политика проекта: не больше 2 бэкапов, имя по
    дате (YYYYMMDD), самый старый удаляется при превышении лимита."""
    backups_dir = PLANTING_COST_PATH.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_path = backups_dir / f"{PLANTING_COST_PATH.stem}_{today}.yaml"
    shutil.copy2(PLANTING_COST_PATH, backup_path)

    existing = sorted(backups_dir.glob(f"{PLANTING_COST_PATH.stem}_*.yaml"))
    while len(existing) > _MAX_PRICE_BACKUPS:
        oldest = existing.pop(0)
        oldest.unlink()
    return backup_path


@app.post("/api/price-list/apply")
def api_price_list_apply(request: PriceListApplyRequest):
    """Применяет подтверждённые пользователем изменения цен ПРЯМО на сервере
    (решение пользователя, 2026-09-19: «сразу применить на сервере, с
    бэкапом» — в отличие от нормативных актов, цены явно помечены как
    редактируемые пользователем, не норма, см. planting_cost_estimates.yaml)."""
    if not request.updates:
        raise HTTPException(status_code=400, detail="Нет ни одного изменения для применения.")

    backup_path = _backup_planting_cost_file()

    with open(PLANTING_COST_PATH, encoding="utf-8") as f:
        current_yaml = yaml.safe_load(f)

    before_after = []
    for upd in request.updates:
        existing = current_yaml["categories"].get(upd.category_key)
        before = dict(existing) if existing else None
        current_yaml["categories"][upd.category_key] = {
            "label_ru": upd.label_ru,
            "min_rub": upd.min_rub,
            "max_rub": upd.max_rub,
            "default_rub": upd.default_rub,
            "note": (existing or {}).get("note", "Добавлено/обновлено из загруженного прайс-листа."),
        }
        before_after.append({"category_key": upd.category_key, "before": before, "after": current_yaml["categories"][upd.category_key]})

    with open(PLANTING_COST_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(current_yaml, f, allow_unicode=True, sort_keys=False)

    log_event("price_list_update", "-", "-", {"updates": [u.model_dump() for u in request.updates]}, {"backup": str(backup_path), "changes": before_after})

    updated_catalog = PlantingCostCatalog()
    return {
        "updated_categories": len(request.updates),
        "backup_file": backup_path.name,
        "source_updated_at": updated_catalog.source_updated_at,
    }


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    # Сервис активно правится вживую во время презентации/разработки — открытая
    # вкладка не должна показывать старый закэшированный app.js/style.css после
    # правки на сервере (реальный случай 2026-09-19: пользователь видел старый
    # баг с маркерами, хотя сервер уже отдавал исправленный код).
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response
