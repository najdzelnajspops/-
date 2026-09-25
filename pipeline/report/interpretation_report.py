"""Interpretation Report Agent — машиночитаемый JSON-отчёт (MVP).

См. docs/AGENTS_PLAN.md, агент №8; docs/REPORT_DESIGN.md (форма вывода —
машиночитаемый JSON, основной формат, обязателен по ТЗ п.3.3: обоснование для
каждой предложенной/отклонённой посадки со ссылкой на конкретный НПА и пункт).

Строит отчёт из ДВУХ уже посчитанных структур, не сочиняет ничего заново:
- ConstraintMapResult (pipeline/constraints/buffer_engine.py) — какие нормы
  применены и с каким статусом verified для данного вида посадки на этом участке;
- PlacementResult (pipeline/placement/generator.py) — конкретные точки, вид
  растения, обоснование выбора вида.

ИЗВЕСТНОЕ УПРОЩЕНИЕ MVP (см. docs/OPEN_QUESTIONS.md): в разделе "обоснования"
для каждой точки перечисляются ВСЕ применённые к этому виду посадки нормы
(с их отступами и статусом verified), а не персональное "фактическое расстояние
до конкретного ближайшего ограничения" для каждой отдельной точки — по
docs/REPORT_DESIGN.md §5 это отдельная колонка, которая требует пересчёта
расстояния от точки до ИСХОДНОЙ (не буферизованной) геометрии каждого объекта-
ограничения; ConstraintMapResult сейчас хранит только буферизованную геометрию
зоны (ZoneSource.buffer_geometry), не исходную геометрию объекта — доработка
оставлена на следующий проход, не выдаётся здесь за уже посчитанную величину.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.otk.boundary_plausibility import BoundaryPlausibilityCheck
from pipeline.otk.citation_integrity import check_citation_integrity
from pipeline.otk.layer_integrity import LayerIntegrityCheck
from pipeline.otk.offset_compliance import OffsetComplianceCheck
from pipeline.placement.generator import TIE_BREAK_EXPLANATION, PlacementResult


def build_report(
    territory_category: str,
    constraint_results: list[ConstraintMapResult],
    placement_results: list[PlacementResult],
    source_files: list[str],
    site_boundary_status: str = "found",
    boundary_plausibility: BoundaryPlausibilityCheck | None = None,
    layer_integrity: LayerIntegrityCheck | None = None,
    offset_compliance: dict[str, OffsetComplianceCheck] | None = None,
    lep_unknown_voltage_count: int = 0,
    lep_unknown_voltage_fallback_m: float | None = None,
) -> dict:
    # ВАЖНО (найдено 2026-09-16 на реальном объекте — «Олимпийская деревня»,
    # 140447 объектов-ограничений): раньше применённые нормы (`applied_norms`,
    # список ДО 140k+ записей) вкладывались КОПИЕЙ В КАЖДУЮ точку отчёта.
    # В памяти это одна и та же ссылка на список (дёшево), но json.dump()
    # сериализует ссылки как полные копии — при тысячах точек получился JSON
    # весом 168 ГБ (!) вместо ожидаемых мегабайт, и прогон завис на десятки
    # минут на одной сериализации. Исправлено: применённые нормы хранятся ОДИН
    # РАЗ на вид посадки в `applied_constraint_norms_by_kind` (верхний уровень
    # отчёта), точка ссылается на них через уже имеющееся поле `planting_kind`,
    # а не несёт копию.
    applied_norms_by_kind: dict[str, list[dict]] = {}
    for cr in constraint_results:
        applied_norms_by_kind[cr.planting_kind] = cr.to_report_rows()

    placements_out = []
    for pr in placement_results:
        for point in pr.points:
            placements_out.append(
                {
                    "id": point.id,
                    "planting_kind": point.planting_kind,
                    "x": point.x,
                    "y": point.y,
                    "status": point.status,
                    "territory_category": pr.territory_category,
                    "species_name_ru": point.species_name_ru,
                    "species_citation": point.species_citation,
                    "grid_spacing_m": point.grid_spacing_m,
                    "grid_spacing_citation": point.grid_spacing_citation,
                }
            )

    # Агент ОТК, проверка 6.3 — независимая от Constraint Engine/Placement Generator
    # сверка: каждая цитата в уже собранных applied_norms_by_kind/placements_out
    # пересчитывается заново из свежего экземпляра соответствующего верифицированного
    # реестра (см. pipeline/otk/citation_integrity.py). Считается здесь (а не
    # прокидывается параметром снаружи, как boundary_plausibility/layer_integrity),
    # потому что ей нужны только эти два уже построенных поля самого отчёта.
    citation_integrity = check_citation_integrity(
        {"applied_constraint_norms_by_kind": applied_norms_by_kind, "placements": placements_out}
    )

    species_choices = [
        {
            "planting_kind": pr.planting_kind,
            "chosen_species_name_ru": pr.chosen_species_entry.name_ru if pr.chosen_species_entry else None,
            # Многовидовой выбор (2026-09-24, чек-боксы на вкладке автогенерации
            # веб-UI) — ВСЕ реально распределённые по точкам виды, в порядке
            # round-robin (см. pipeline/placement/generator.py). При однвидовом
            # режиме (по умолчанию) содержит либо [chosen_species_name_ru], либо
            # [] — согласовано с полем выше, не отдельная, потенциально
            # расходящаяся правда.
            "chosen_species_names_ru": [e.name_ru for e in pr.chosen_species_entries],
            "species_conflict_note": pr.species_conflict_note,
            "eligible_alternatives": pr.eligible_alternatives,
            "tie_break_explanation": (TIE_BREAK_EXPLANATION if pr.eligible_alternatives else None),
            "density_note": pr.density_note,
            "estimated_cost_rub": pr.estimated_cost_rub,
        }
        for pr in placement_results
    ]
    total_estimated_cost_rub = sum(pr.estimated_cost_rub or 0 for pr in placement_results)

    summary = {
        "total_points": len(placements_out),
        "placed": sum(1 for p in placements_out if p["status"] == "placed"),
        "rejected": sum(1 for p in placements_out if p["status"] != "placed"),
        "by_planting_kind": {
            pr.planting_kind: {
                "placed": len(pr.placed_points()),
                "rejected": len(pr.rejected_points()),
            }
            for pr in placement_results
        },
    }

    known_simplifications = [
        "applied_constraint_norms_by_kind (один раз на вид посадки, не на точку — см. "
        "комментарий в коде про баг 168 ГБ от 2026-09-16) перечисляет ВСЕ нормы, "
        "применённые к данному виду посадки на участке, а не фактическое расстояние "
        "от КОНКРЕТНОЙ точки до ближайшего ограничения (docs/REPORT_DESIGN.md §5) — "
        "доработка оставлена на следующий проход.",
        "Совместимость посадок учитывается только по одному верифицированному правилу "
        "(сноска [6] каталога ДПиООС — можжевельник/яблоня-груша-айва, ржавчинные грибы, "
        "см. species_choices ниже). Более широкая совместимость/аллелопатия и требования "
        "к освещённости (теневая сторона зданий) не учитываются — нет верифицированного "
        "источника (docs/ARCHITECTURE.md §3.1c, docs/OPEN_QUESTIONS.md).",
        "estimated_cost_rub — оценка по data/reference/planting_cost_estimates.yaml, "
        "НЕ прайс-лист организаторов (устное упоминание, что цена зависит от питомника) — "
        "значения редактируемы человеком в этом файле. Целевой бюджет (10-25 млн руб/га) "
        "тоже устное упоминание организаторов, не документ/норма — используется только для "
        "справочного сравнения в CLI-выводе, НЕ ограничивает густоту посадки (проверено на "
        "реальном объекте: диапазон оказался значительно шире настоящей нормы плотности ниже).",
        "Густота кустарника определяется верифицированной нормой плотности (623-ПП/МГСН 1.02-02, "
        "Таблица В.1, шт/га по категории территории, см. species_choices[].density_note и "
        "pipeline/placement/density.py), размещается небольшими компактными группами "
        "(СП82.13330.2016 п.9.30), а не сплошной сеткой по всей территории.",
    ]
    if site_boundary_status != "found":
        known_simplifications.insert(
            0,
            f"site_boundary_status = {site_boundary_status!r} — граница участка НЕ найдена "
            "надёжно (см. docs/DATA_STRUCTURE.md §11). Нулевое или маленькое число "
            "предложенных посадок ниже может объясняться именно этим, а не тем, что "
            "участок действительно весь занят ограничениями — площадь допустимой зоны "
            "нужно проверить относительно реальных размеров участка вручную, прежде "
            "чем доверять итогу как есть.",
        )
    if boundary_plausibility is not None and boundary_plausibility.verdict == "implausible":
        known_simplifications.insert(0, f"[Агент ОТК, 6.1a] {boundary_plausibility.explanation}")
    if lep_unknown_voltage_count > 0:
        known_simplifications.insert(
            0,
            f"ВНИМАНИЕ: на участке {lep_unknown_voltage_count} объектов ЛЭП, чей класс напряжения "
            "не удалось определить по имени слоя чертежа (см. pipeline/constraints/lep_zones.py) — "
            f"каждому из них проставлен МАКСИМАЛЬНО КОНСЕРВАТИВНЫЙ отступ "
            f"{lep_unknown_voltage_fallback_m:.0f} м (самый широкий тариф Постановления Правительства "
            "РФ №160 от 24.02.2009 — для воздушных линий 1150 кВ) — честный отказ от угадывания "
            "класса напряжения, а не подтверждённая норма для ЭТОЙ конкретной ЛЭП. Реальный класс "
            "напряжения городской ЛЭП почти наверняка ниже (что дало бы отступ от 2 до 40 м вместо "
            f"{lep_unknown_voltage_fallback_m:.0f} м), но чертёж не даёт способа его определить. "
            "Это может критически уменьшить итоговую допустимую зону для посадки вплоть до почти "
            "нулевой (реальный случай, 2026-09-17: 55 м от одной такой ЛЭП дали запрещённую зону, "
            "в 3,7 раза превышающую площадь всего участка) — маленькое/нулевое число посадок ниже "
            "может объясняться именно этим, а не реальной занятостью участка.",
        )
    if offset_compliance is not None:
        for kind, check in offset_compliance.items():
            if check.verdict == "violation":
                known_simplifications.insert(0, f"[Агент ОТК, 6.1, {kind}] {check.explanation}")
    if citation_integrity.verdict == "violation":
        known_simplifications.insert(0, f"[Агент ОТК, 6.3] {citation_integrity.explanation}")

    return {
        "schema_version": "1.1-mvp",
        "territory_category": territory_category,
        "source_files": source_files,
        "site_boundary_status": site_boundary_status,
        "lep_unknown_voltage": {
            "count": lep_unknown_voltage_count,
            "fallback_distance_m": lep_unknown_voltage_fallback_m,
            "explanation": (
                "Класс напряжения не определён по чертежу — применён максимально консервативный "
                "тариф Постановления №160 (1150 кВ), а не подтверждённая норма для этой ЛЭП. См. "
                "known_simplifications для полного объяснения."
                if lep_unknown_voltage_count > 0
                else "На участке нет ЛЭП с неопределённым классом напряжения."
            ),
        },
        "applied_constraint_norms_by_kind": applied_norms_by_kind,
        "otk_boundary_plausibility": (
            {
                "verdict": boundary_plausibility.verdict,
                "boundary_area_m2": boundary_plausibility.boundary_area_m2,
                "features_bbox_area_m2": boundary_plausibility.features_bbox_area_m2,
                "ratio": boundary_plausibility.ratio,
                "explanation": boundary_plausibility.explanation,
            }
            if boundary_plausibility is not None
            else None
        ),
        "otk_layer_integrity": (
            {
                "verdict": layer_integrity.verdict,
                "base_entity_count": layer_integrity.base_entity_count,
                "output_entity_count": layer_integrity.output_entity_count,
                "new_layers_found": layer_integrity.new_layers_found,
                "explanation": layer_integrity.explanation,
                "issues": [
                    {"kind": issue.kind, "layer": issue.layer, "detail": issue.detail}
                    for issue in layer_integrity.issues
                ],
            }
            if layer_integrity is not None
            else None
        ),
        "otk_offset_compliance": (
            {
                kind: {
                    "verdict": check.verdict,
                    "points_checked": check.points_checked,
                    "explanation": check.explanation,
                    "violations": [
                        {
                            "point_id": v.point_id,
                            "kind": v.kind,
                            "feature_id": v.feature_id,
                            "boundary_type": v.boundary_type,
                            "required_distance_m": v.required_distance_m,
                            "actual_distance_m": v.actual_distance_m,
                            "detail": v.detail,
                        }
                        for v in check.violations
                    ],
                }
                for kind, check in offset_compliance.items()
            }
            if offset_compliance is not None
            else None
        ),
        "otk_citation_integrity": {
            "verdict": citation_integrity.verdict,
            "citations_checked": citation_integrity.citations_checked,
            "explanation": citation_integrity.explanation,
            "violations": [
                {"location": v.location, "kind": v.kind, "detail": v.detail}
                for v in citation_integrity.violations
            ],
        },
        "summary": summary,
        "total_estimated_cost_rub": total_estimated_cost_rub,
        "species_choices": species_choices,
        "placements": placements_out,
        "known_simplifications": known_simplifications,
    }


def write_report(
    output_path: str | Path,
    territory_category: str,
    constraint_results: list[ConstraintMapResult],
    placement_results: list[PlacementResult],
    source_files: list[str],
    site_boundary_status: str = "found",
    boundary_plausibility: BoundaryPlausibilityCheck | None = None,
    layer_integrity: LayerIntegrityCheck | None = None,
    offset_compliance: dict[str, OffsetComplianceCheck] | None = None,
    lep_unknown_voltage_count: int = 0,
    lep_unknown_voltage_fallback_m: float | None = None,
) -> dict:
    report = build_report(
        territory_category,
        constraint_results,
        placement_results,
        source_files,
        site_boundary_status,
        boundary_plausibility,
        layer_integrity,
        offset_compliance,
        lep_unknown_voltage_count,
        lep_unknown_voltage_fallback_m,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
