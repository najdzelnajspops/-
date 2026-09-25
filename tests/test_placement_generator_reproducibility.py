"""Повторяемость результата Placement Generator (pipeline/placement/generator.py).

См. docs/ARCHITECTURE.md, §3.1 «Повторяемость результата (обязательное требование
к демо)»: tests/test_reproducibility.py уже доказывает это для Constraint Engine,
но явно оставлял Placement Generator как «ещё не написан, тестировать отдельно,
когда появится» (см. докстринг того файла) — этот пробел был осознанным, не
случайным, и остался непокрытым до сих пор, несмотря на то что генератор написан
уже давно. Закрывается здесь тем же методом: два (и десять) независимых, ПОЛНЫХ
вызова generate_placement() с одним и тем же входом обязаны дать структурно
идентичный результат — не «похожий», а `==` по всему PlacementResult (порядок
точек в списке важен, т.к. напрямую попадает в JSON-отчёт, см. docs/REPORT_DESIGN.md).

Два сценария намеренно разные по алгоритму генерации кандидатов (см. докстринг
generate_placement): сплошная сетка (`_grid_candidates`, ветка для дерева) и
кластеризация небольшими группами (`_cluster_candidates`, ветка для кустарника,
cluster_config задан) — оба пути должны быть одинаково детерминированы.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import GeometryCollection, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.placement.generator import ClusterConfig, generate_placement


def _make_constraint_result(planting_kind: str, allowed_zone) -> ConstraintMapResult:
    return ConstraintMapResult(
        planting_kind=planting_kind,
        site_boundary=allowed_zone,
        allowed_zone=allowed_zone,
        forbidden_zone=GeometryCollection(),
        zone_sources=[],
        skipped_features=[],
        unknown_boundary_types=set(),
    )


def _run_tree_grid_once():
    # Достаточно большой участок с "рваной" формой (не идеальный прямоугольник) —
    # чтобы strict-containment (см. test_placement_generator.py) отбрасывал разное
    # число узлов сетки по краям и реальнее моделировал реальный объект.
    allowed = box(0, 0, 43, 29)
    result = _make_constraint_result("tree", allowed)
    registry = OffsetRegistry()
    return generate_placement(
        constraint_result=result,
        territory_category="dvorovye",
        life_form="tree",
        species_catalog=DpioosSpeciesCatalog(),
        invasive_registry=InvasiveSpeciesRegistry(),
        planting_density=registry.planting_density,
    )


def _run_shrub_clusters_once():
    allowed = box(0, 0, 43, 29)
    result = _make_constraint_result("shrub", allowed)
    registry = OffsetRegistry()
    cluster_config = ClusterConfig(group_size=7, group_spacing_m=8.0, intra_group_spacing_m=0.5)
    return generate_placement(
        constraint_result=result,
        territory_category="dvorovye",
        life_form="shrub",
        species_catalog=DpioosSpeciesCatalog(),
        invasive_registry=InvasiveSpeciesRegistry(),
        planting_density=registry.planting_density,
        cluster_config=cluster_config,
    )


def test_tree_grid_placement_is_reproducible():
    result_a = _run_tree_grid_once()
    result_b = _run_tree_grid_once()

    assert len(result_a.placed_points()) > 1  # сценарий должен быть содержательным, не вырожденным
    assert result_a == result_b

    reference = _run_tree_grid_once()
    for _ in range(10):
        assert _run_tree_grid_once() == reference


def test_shrub_cluster_placement_is_reproducible():
    result_a = _run_shrub_clusters_once()
    result_b = _run_shrub_clusters_once()

    assert len(result_a.placed_points()) > 1
    assert result_a == result_b

    reference = _run_shrub_clusters_once()
    for _ in range(10):
        assert _run_shrub_clusters_once() == reference


def test_species_and_notes_are_identical_across_runs():
    """Не только координаты — species_conflict_note/eligible_alternatives/
    density_note тоже обязаны быть стабильны (напрямую видны в отчёте)."""
    reference = _run_shrub_clusters_once()
    for _ in range(5):
        repeat = _run_shrub_clusters_once()
        assert repeat.chosen_species_entry == reference.chosen_species_entry
        assert repeat.species_conflict_note == reference.species_conflict_note
        assert repeat.eligible_alternatives == reference.eligible_alternatives
        assert repeat.density_note == reference.density_note
