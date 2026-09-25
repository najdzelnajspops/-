"""CLI точка входа Service A — полный путь DXF -> анализ -> DXF (MVP).

См. docs/AGENTS_PLAN.md (агенты 2, 4, 5, 7, 8), docs/TASK_BRIEF.md, п.1.
Последовательность (docs/DATA_ALGORITHM.md):

1. DXF Ingest (набор файлов объекта) -> IngestResult (site_boundary + features).
2. Существующие сохраняемые насаждения получают отступ по практической политике
   (pipeline/constraints/existing_vegetation.py) — единственный тип зоны в
   проекте, для которого explicit_citation сознательно остаётся None (verified=False).
3. Constraint/Buffer Engine — отдельно для дерева и кустарника -> допустимая зона.
4. Placement Generator — отдельно для дерева и кустарника, с категорией
   территории как входным параметром (не выводится из геометрии, см.
   pipeline/placement/generator.py).
5. DXF Export Agent — новые слои в НОВЫЙ файл, исходный чертёж не трогается.
6. Interpretation Report Agent — машиночитаемый JSON.

Агент ОТК (docs/AGENTS_PLAN.md, агент №6) — реализованы проверки 6.1–6.4:
- 6.1a (pipeline/otk/boundary_plausibility.py) — независимый пересчёт правдоподобия
  площади границы участка относительно охвата объектов-коммуникаций. Найдена и
  обоснована реальным багом 2026-09-16 (docs/DATA_STRUCTURE.md §11.3).
- 6.1 продолжение (pipeline/otk/offset_compliance.py) — независимая сверка КАЖДОЙ
  точки посадки: фактическое расстояние до сырой геометрии каждого объекта-
  ограничения не меньше требуемого отступа, без обращения к вычисленному
  Constraint Engine allowed_zone.
- 6.2 (pipeline/otk/layer_integrity.py) — исходные слои/сущности DXF не изменены
  результатом экспорта.
- 6.3 (pipeline/otk/citation_integrity.py) — каждая цитата в готовом JSON-отчёте
  пересчитана заново из свежих реестров норм и совпадает буквально; неверифицированная
  строка обязана нести честный плейсхолдер, а не подставленный текст.
- 6.4 (tests/test_service_a_regression.py) — регрессия на эталонных данных
  (golden baseline на реальном объекте); тест, не рантайм-проверка при каждом
  прогоне.
Проверка 6.5 («достаточность данных») в проекте не применяется — она относилась
к разовой рекомендации без полноценной геоосновы (Service B), которая по
решению пользователя (2026-09-24) исключена из объёма проекта — см.
docs/OPEN_QUESTIONS.md.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from math import ceil, sqrt

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog, TerritoryCategory
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.catalog.planting_cost import PlantingCostCatalog
from pipeline.constraints.buffer_engine import ConstraintMapResult, build_constraint_map
from pipeline.constraints.existing_vegetation import ExistingVegetationPolicy
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.constraints.red_lines_policy import apply_red_lines_policy
from pipeline.constraints.offset_registry import OffsetRegistry, PlantingKind
from pipeline.export.dxf_export import ExportSummary, export_placement_to_dxf
from pipeline.ingest.dxf_ingest import IngestResult, ingest_dxf_files
from pipeline.otk.boundary_plausibility import BoundaryPlausibilityCheck, check_boundary_plausibility
from pipeline.otk.layer_integrity import LayerIntegrityCheck, check_layer_integrity
from pipeline.otk.offset_compliance import OffsetComplianceCheck, check_offset_compliance
from pipeline.placement.density import PlantingDensityRegistry
from pipeline.placement.generator import (
    DEFAULT_SHRUB_GROUP_SIZE,
    ClusterConfig,
    PlacementResult,
    generate_placement,
    norm_spacing_for,
)
from pipeline.report.interpretation_report import write_report

PLANTING_KINDS: list[PlantingKind] = ["tree", "shrub"]


@dataclass
class ServiceARunResult:
    ingest_result: IngestResult
    boundary_plausibility: BoundaryPlausibilityCheck | None = None
    layer_integrity: LayerIntegrityCheck | None = None
    offset_compliance: dict[str, OffsetComplianceCheck] = field(default_factory=dict)
    constraint_results: dict[str, ConstraintMapResult] = field(default_factory=dict)
    placement_results: dict[str, PlacementResult] = field(default_factory=dict)
    export_summary: ExportSummary | None = None
    report: dict | None = None
    target_budget_min_rub: float = 0.0
    target_budget_max_rub: float = 0.0
    lep_unknown_voltage_fallback_m: float | None = None


def run_service_a(
    input_dxf_paths: list[str | Path],
    base_dxf_path_for_export: str | Path,
    territory_category: TerritoryCategory,
    output_dxf_path: str | Path,
    output_report_path: str | Path,
    selected_species_names: dict[str, list[str]] | None = None,
) -> ServiceARunResult:
    """selected_species_names (2026-09-24, чек-боксы видов на вкладке
    автогенерации веб-UI): {"tree": [...], "shrub": [...]} — ключ жизненной
    формы, отсутствующей в словаре (или сам словарь None), сохраняет прежнее
    однвидовое поведение для этой формы (pick_species), см. generate_placement."""
    ingest_result = ingest_dxf_files(input_dxf_paths)

    if ingest_result.site_boundary is None:
        raise ValueError(
            "Граница участка не найдена во входных файлах (site_boundary_status="
            f"{ingest_result.site_boundary_status!r}) — без неё Constraint Engine "
            "не может построить допустимую зону. См. docs/DATA_STRUCTURE.md §11."
        )

    veg_policy = ExistingVegetationPolicy()
    features = veg_policy.apply_to_features(ingest_result.features)
    # Красные линии — зона запрета размещения насаждений (решение пользователя
    # 2026-09-17, не норма 743-ПП; см. pipeline/constraints/red_lines_policy.py).
    features = apply_red_lines_policy(features)

    # ВАЖНО, найдено на реальном объекте 2026-09-17 (см. docs/SESSION_LOG.md):
    # если на участке есть воздушная ЛЭП, чей класс напряжения не удалось
    # определить по имени слоя (lep_features_needing_voltage — заполняется в
    # dxf_ingest.py), ей проставляется САМЫЙ ШИРОКИЙ тариф Постановления №160
    # (1150 кВ, most_conservative_width_m()) — честный консервативный отказ
    # вместо угадывания, но на практике способен «съесть» почти весь участок
    # (на «Камчатской улице» — 369% площади участка от одной этой зоны). Это
    # не баг, а прямое следствие отсутствия данных о реальном классе напряжения
    # — но пользователь отчёта ОБЯЗАН это увидеть явно, а не только косвенно
    # через маленькую итоговую allowed_zone без объяснения причины.
    lep_unknown_voltage_fallback_m = (
        LepZoneRegistry().most_conservative_width_m() if ingest_result.lep_features_needing_voltage else None
    )

    # Агент ОТК, проверка 6.1a — независимый пересчёт ДО того, как этот же
    # site_boundary используется дальше в Constraint Engine. Работает на сырых
    # ingest_result.features, не на внутренних переменных генератора.
    boundary_plausibility = check_boundary_plausibility(ingest_result.site_boundary, ingest_result.features)

    offset_registry = OffsetRegistry()
    species_catalog = DpioosSpeciesCatalog()
    invasive_registry = InvasiveSpeciesRegistry()
    cost_catalog = PlantingCostCatalog()
    density_registry = PlantingDensityRegistry()

    constraint_results: dict[str, ConstraintMapResult] = {}
    placement_results: dict[str, PlacementResult] = {}
    offset_compliance: dict[str, OffsetComplianceCheck] = {}

    # Целевой диапазон стоимости озеленения (2026-09-16, устное указание
    # организаторов, НЕ норма, см. data/reference/planting_cost_estimates.yaml) —
    # 10-25 млн руб/га. Используется только как СПРАВОЧНОЕ сравнение в отчёте —
    # не ограничивает густоту (проверено на реальном объекте: этот диапазон
    # оказался значительно шире, чем позволяет настоящая норма плотности ниже,
    # см. docs/SESSION_LOG.md, 2026-09-16 — бюджет как ограничитель density не
    # сработал, реальный ограничитель — 623-ПП).
    site_area_ha = ingest_result.site_boundary.area / 10_000
    target_budget_min_rub = site_area_ha * cost_catalog.target_budget_per_hectare_min_rub
    target_budget_max_rub = site_area_ha * cost_catalog.target_budget_per_hectare_max_rub

    # Дерево — по норме 743-ПП (сплошная сетка с верифицированным шагом; на
    # реальных объектах уже даёт правдоподобное число, заметно ниже потолка
    # 623-ПП). Кустарник — небольшими группами (ClusterConfig), число которых
    # подобрано под целевую густоту 623-ПП, Таблица В.1 (pipeline/placement/density.py) —
    # взамен чисто геометрического шага сетки, который на большом участке давал
    # нереалистичное число экземпляров (см. docs/OPEN_QUESTIONS.md, 2026-09-16).
    # Выбор вида дерева передаётся в подбор вида кустарника для проверки
    # фитосанитарного конфликта (сноска [6] ДПиООС).
    previously_chosen_species: list = []
    for kind in PLANTING_KINDS:
        constraint_result = build_constraint_map(
            site_boundary=ingest_result.site_boundary,
            features=features,
            planting_kind=kind,
            registry=offset_registry,
        )
        constraint_results[kind] = constraint_result

        cluster_config = None
        density_note = None
        if kind == "shrub":
            allowed_area_m2 = constraint_result.allowed_zone.area
            target_count = density_registry.target_count_for_area(territory_category, kind, allowed_area_m2)
            intra_spacing_m = norm_spacing_for(kind, offset_registry.planting_density)
            num_groups = max(1, round(target_count / DEFAULT_SHRUB_GROUP_SIZE))
            group_footprint_m = intra_spacing_m * ceil(sqrt(DEFAULT_SHRUB_GROUP_SIZE))
            group_spacing_m = max(
                sqrt(allowed_area_m2 / num_groups) if allowed_area_m2 > 0 else group_footprint_m,
                group_footprint_m,
            )
            cluster_config = ClusterConfig(
                group_size=DEFAULT_SHRUB_GROUP_SIZE,
                group_spacing_m=group_spacing_m,
                intra_group_spacing_m=intra_spacing_m,
            )
            density_note = (
                f"Густота ограничена нормой плотности (623-ПП/МГСН 1.02-02, Таблица В.1): целевое "
                f"число ~{target_count} шт. на участке размещено небольшими группами по "
                f"{DEFAULT_SHRUB_GROUP_SIZE} шт. (расстояние внутри группы {intra_spacing_m:.2f} м — "
                "743-ПП, Таблица 3.6.2), а не сплошной сеткой по всей территории — см. "
                "docs/SESSION_LOG.md, 2026-09-16."
            )

        placement_result = generate_placement(
            constraint_result=constraint_result,
            territory_category=territory_category,
            life_form=kind,  # PlantingKind и LifeForm — одни и те же две строки
            species_catalog=species_catalog,
            invasive_registry=invasive_registry,
            planting_density=offset_registry.planting_density,
            avoid_conflict_with=previously_chosen_species,
            cluster_config=cluster_config,
            density_note=density_note,
            selected_species_names=(selected_species_names or {}).get(kind),
        )
        if placement_result.chosen_species_entries:
            # Сумма по КАЖДОЙ точке отдельно (не count*unit_cost) — в
            # многовидовом режиме (2026-09-24) у разных точек может быть разный
            # вид с разной стоимостью; тот же принцип, что и в
            # pipeline/review/placement_review.py::_estimate_cost для ручного
            # ввода. При одном виде даёт то же число, что и раньше.
            cost_by_name = {e.name_ru: cost_catalog.default_cost_for(e) for e in placement_result.chosen_species_entries}
            placement_result.estimated_cost_rub = sum(
                cost_by_name.get(p.species_name_ru, 0.0) for p in placement_result.placed_points()
            )

        placement_results[kind] = placement_result
        previously_chosen_species = placement_result.chosen_species_entries

        # Агент ОТК, проверка 6.1 (продолжение) — независимая от build_constraint_map/
        # generate_placement сверка: пересчитывает фактическое расстояние каждой точки
        # до сырой (небуферизованной) геометрии объектов-ограничений напрямую, не через
        # constraint_result.allowed_zone (см. pipeline/otk/offset_compliance.py).
        offset_compliance[kind] = check_offset_compliance(
            site_boundary=ingest_result.site_boundary,
            features=features,
            registry=offset_registry,
            planting_kind=kind,
            points=placement_result.points,
        )

    export_summary = export_placement_to_dxf(
        base_dxf_path=base_dxf_path_for_export,
        output_path=output_dxf_path,
        placement_results=list(placement_results.values()),
    )

    # Агент ОТК, проверка 6.2 — независимая от pipeline/export/dxf_export.py
    # проверка целостности слоёв: открывает оба файла заново, не переиспользует
    # внутренние переменные Export Agent (см. pipeline/otk/layer_integrity.py).
    layer_integrity = check_layer_integrity(base_dxf_path_for_export, output_dxf_path)

    report = write_report(
        output_path=output_report_path,
        territory_category=territory_category,
        constraint_results=list(constraint_results.values()),
        placement_results=list(placement_results.values()),
        source_files=ingest_result.source_files,
        site_boundary_status=ingest_result.site_boundary_status,
        boundary_plausibility=boundary_plausibility,
        layer_integrity=layer_integrity,
        offset_compliance=offset_compliance,
        lep_unknown_voltage_count=len(ingest_result.lep_features_needing_voltage),
        lep_unknown_voltage_fallback_m=lep_unknown_voltage_fallback_m,
    )

    return ServiceARunResult(
        ingest_result=ingest_result,
        boundary_plausibility=boundary_plausibility,
        layer_integrity=layer_integrity,
        offset_compliance=offset_compliance,
        constraint_results=constraint_results,
        placement_results=placement_results,
        export_summary=export_summary,
        report=report,
        target_budget_min_rub=target_budget_min_rub,
        target_budget_max_rub=target_budget_max_rub,
        lep_unknown_voltage_fallback_m=lep_unknown_voltage_fallback_m,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проект озеленения территории — Service A: DXF -> анализ -> DXF (MVP)."
    )
    parser.add_argument(
        "--input", nargs="+", required=True, help="Путь(и) к DXF-файлам объекта (весь бандл: главный + up/tp/kl)."
    )
    parser.add_argument(
        "--base-dxf",
        required=True,
        help="Файл бандла, В КОТОРЫЙ добавляются новые слои результата (обычно топоплан/файл с границей участка).",
    )
    parser.add_argument(
        "--territory-category",
        required=True,
        choices=[
            "dvorovye", "doshkolnye", "obscheobr", "zdravoohr",
            "magistrali", "ploschadi", "parki", "proizvodstvennye",
        ],
        help="Категория территории (ДПиООС, 8 категорий) — параметр запуска, не выводится из DXF.",
    )
    parser.add_argument("--output-dxf", required=True, help="Куда сохранить итоговый DXF (новый файл).")
    parser.add_argument("--output-report", required=True, help="Куда сохранить JSON-отчёт интерпретации.")
    parser.add_argument(
        "--output-human-report-dir",
        default=None,
        help=(
            "Необязательно: каталог для человекочитаемого отчёта (Markdown + HTML + схемы "
            "«до/после»), см. pipeline/report/human_report.py и docs/REPORT_DESIGN.md. Без "
            "этого флага строится только машиночитаемый JSON (--output-report)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    result = run_service_a(
        input_dxf_paths=args.input,
        base_dxf_path_for_export=args.base_dxf,
        territory_category=args.territory_category,
        output_dxf_path=args.output_dxf,
        output_report_path=args.output_report,
    )

    print(f"Источники: {result.ingest_result.source_files}")
    print(f"Граница участка: {result.ingest_result.site_boundary_status}")
    if result.ingest_result.site_boundary_status != "found":
        print(
            "  ВНИМАНИЕ: граница участка определена ненадёжно (см. docs/DATA_STRUCTURE.md §11) — "
            "малое/нулевое число посадок ниже может быть следствием этого, а не реальной "
            "занятости участка. Проверьте площадь допустимой зоны вручную."
        )
    if result.boundary_plausibility is not None:
        print(f"  [Агент ОТК, 6.1a] правдоподобие границы: {result.boundary_plausibility.verdict}")
        if result.boundary_plausibility.verdict != "ok":
            print(f"    ВНИМАНИЕ: {result.boundary_plausibility.explanation}")
    lep_count = len(result.ingest_result.lep_features_needing_voltage)
    if lep_count > 0:
        print(
            f"  ВНИМАНИЕ: на участке {lep_count} объектов ЛЭП, чей класс напряжения не удалось "
            f"определить по чертежу — применён максимально консервативный отступ "
            f"{result.lep_unknown_voltage_fallback_m:.0f} м (тариф для линий 1150 кВ, Постановление "
            "№160) для КАЖДОГО из них. Реальный класс напряжения этой ЛЭП, скорее всего, ниже — но "
            "чертёж не даёт способа его определить. Это может критически уменьшить итоговую "
            "допустимую зону для посадки (см. известный случай на «Камчатской улице» — "
            "docs/SESSION_LOG.md, 2026-09-17: 55 м от одной ЛЭП дали зону, кратно превышающую сам "
            "участок)."
        )
    for kind, placement in result.placement_results.items():
        print(
            f"  {kind}: предложено {len(placement.placed_points())}, "
            f"отклонено {len(placement.rejected_points())} (нет рекомендованного вида)"
        )
        if placement.chosen_species_entry:
            print(f"    вид: {placement.chosen_species_entry.name_ru}")
        if placement.eligible_alternatives:
            print(f"    равно допустимые альтернативы: {', '.join(placement.eligible_alternatives)}")
        if placement.species_conflict_note:
            print(f"    {placement.species_conflict_note}")
        if placement.density_note:
            print(f"    [густота] {placement.density_note}")
        if placement.estimated_cost_rub is not None:
            print(f"    оценочная стоимость: {placement.estimated_cost_rub:,.0f} руб.".replace(",", " "))
        offset_check = result.offset_compliance.get(kind)
        if offset_check is not None:
            print(f"    [Агент ОТК, 6.1] отступы от коммуникаций: {offset_check.verdict}")
            if offset_check.verdict != "ok":
                print(f"      ВНИМАНИЕ: {offset_check.explanation}")
                for violation in offset_check.violations:
                    print(f"        ({violation.kind}): {violation.detail}")
    total_cost_rub = sum(p.estimated_cost_rub or 0 for p in result.placement_results.values())
    print(f"Оценочная стоимость озеленения (все виды посадки): {total_cost_rub:,.0f} руб.".replace(",", " "))
    print(
        f"  Целевой диапазон (устное указание организаторов, 10-25 млн руб/га): "
        f"{result.target_budget_min_rub:,.0f} - {result.target_budget_max_rub:,.0f} руб.".replace(",", " ")
    )
    if not (result.target_budget_min_rub <= total_cost_rub <= result.target_budget_max_rub):
        print(
            "  ПРИМЕЧАНИЕ: оценочная стоимость вне целевого диапазона — это ожидаемо и НЕ ошибка: "
            "густота посадки определяется нормой плотности (623-ПП), а не бюджетом, см. docs/OPEN_QUESTIONS.md."
        )
    print(f"DXF-результат: {result.export_summary.output_path}")
    if result.layer_integrity is not None:
        print(f"  [Агент ОТК, 6.2] целостность слоёв: {result.layer_integrity.verdict}")
        if result.layer_integrity.verdict != "ok":
            for issue in result.layer_integrity.issues:
                print(f"    ВНИМАНИЕ ({issue.kind}, слой {issue.layer}): {issue.detail}")
    citation_integrity = (result.report or {}).get("otk_citation_integrity")
    if citation_integrity is not None:
        print(f"  [Агент ОТК, 6.3] целостность цитат: {citation_integrity['verdict']}")
        if citation_integrity["verdict"] != "ok":
            for violation in citation_integrity["violations"]:
                print(f"    ВНИМАНИЕ ({violation['kind']}, {violation['location']}): {violation['detail']}")
    print(f"Отчёт (JSON): {args.output_report}")

    if args.output_human_report_dir:
        from pipeline.report.human_report import render_human_report

        human_paths = render_human_report(
            report=result.report,
            constraint_results=result.constraint_results,
            placement_results=result.placement_results,
            output_dir=args.output_human_report_dir,
        )
        print(f"Человекочитаемый отчёт (Markdown): {human_paths.markdown_path}")
        print(f"Человекочитаемый отчёт (HTML): {human_paths.html_path}")


if __name__ == "__main__":
    main()
