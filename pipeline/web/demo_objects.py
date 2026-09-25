"""Реестр демо-объектов для веб-UI (pipeline/web/).

Файлы — заранее сконвертированные DXF (см. data/demo_objects/), не сырые DWG:
живая презентация не должна зависеть от того, установлен ли на машине, где
запущен веб-сервис, ODA File Converter (лицензируемый, отдельная установка,
см. docker/oda/README.md) или доступен ли 23-ГБ сырой датасет (вне git). CLI
(pipeline/run.py, pipeline/review_run.py) по-прежнему работает и с DWG через
pipeline/ingest/dwg_convert.py — это ограничение только веб-демо.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEMO_OBJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "demo_objects"


@dataclass(frozen=True)
class DemoObject:
    id: str
    label_ru: str
    files: tuple[str, ...]  # относительно DEMO_OBJECTS_DIR/<id>/
    base_dxf: str  # какой из files — тот, в который добавляются новые слои результата
    default_territory_category: str


DEMO_OBJECTS: dict[str, DemoObject] = {
    "peschany_pereulok": DemoObject(
        id="peschany_pereulok",
        label_ru="Песчаный переулок",
        files=(
            "output_1-5__3_ДЖКХ-25_00752tp.dxf",
            "output_1-5__3_ДЖКХ-25_00752up.dxf",
            "output_1-5__brd.dxf",
        ),
        base_dxf="output_1-5__3_ДЖКХ-25_00752tp.dxf",
        default_territory_category="dvorovye",
    ),
    # Добавлены 2026-09-24 (прямой запрос пользователя — увидеть работу выбора
    # объекта в веб-UI не на единственном варианте). Границы/площадь сверены
    # с уже подтверждённым цензом docs/DATA_STRUCTURE.md §11.2 (Багрицкого —
    # 88 739,5 м² против задокументированных 88 740 м², Макеева С. ул —
    # 93 155,96 м² против 93 156 м² — совпадение, не совпадение случайно).
    # «Грузинская М ул» намеренно НЕ включена — при её ingest агент ОТК 6.1a
    # честно возвращает implausible (ratio 0.00064): реальный выброс в исходных
    # DWG (объект-коммуникация далеко за пределами участка раздувает bbox до
    # ~61 км² при площади границы 3,9 га) — тот же класс проблемы, что уже
    # найден и осознанно припаркован для «Кустанайской улицы»
    # (docs/DATA_STRUCTURE.md §11.5), не форсируем в демо без ручной проверки
    # исходника.
    "bagritskogo_ulitsa": DemoObject(
        id="bagritskogo_ulitsa",
        label_ru="Багрицкого улица",
        files=(
            "bagritskogo_tp.dxf",
            "bagritskogo_up.dxf",
            "bagritskogo_brd.dxf",
        ),
        base_dxf="bagritskogo_tp.dxf",
        default_territory_category="dvorovye",
    ),
    "makeeva_s_ulitsa": DemoObject(
        id="makeeva_s_ulitsa",
        label_ru="Макеева С. ул",
        files=(
            "makeeva_tp.dxf",
            "makeeva_up.dxf",
            "makeeva_brd.dxf",
        ),
        base_dxf="makeeva_tp.dxf",
        default_territory_category="dvorovye",
    ),
}


def list_demo_objects() -> list[DemoObject]:
    return list(DEMO_OBJECTS.values())


def get_demo_object(demo_id: str) -> DemoObject:
    obj = DEMO_OBJECTS.get(demo_id)
    if obj is None:
        raise KeyError(f"Демо-объект {demo_id!r} не найден")
    return obj


def resolve_input_paths(obj: DemoObject) -> list[Path]:
    base_dir = DEMO_OBJECTS_DIR / obj.id
    return [base_dir / f for f in obj.files]


def resolve_base_dxf(obj: DemoObject) -> Path:
    return DEMO_OBJECTS_DIR / obj.id / obj.base_dxf
