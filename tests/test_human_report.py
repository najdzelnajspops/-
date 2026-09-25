"""Тесты человекочитаемого отчёта (pipeline/report/human_report.py) — прямой
ответ на разрыв, найденный при анализе требований ТЗ (2026-09-23): JSON-отчёт
полный, но без CAD/умения читать JSON результат физически не увидеть."""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.placement.generator import PlacementPoint, PlacementResult
from pipeline.report.human_report import render_human_report
from pipeline.report.interpretation_report import build_report


def _make_fixture():
    site_boundary = box(0, 0, 100, 100)
    forbidden = box(0, 0, 30, 100)
    allowed = site_boundary.difference(forbidden)

    constraint_results = {
        "tree": ConstraintMapResult(
            planting_kind="tree", site_boundary=site_boundary, allowed_zone=allowed, forbidden_zone=forbidden
        ),
        "shrub": ConstraintMapResult(
            planting_kind="shrub", site_boundary=site_boundary, allowed_zone=allowed, forbidden_zone=forbidden
        ),
    }

    tree_points = [
        PlacementPoint(
            id="tree_0001", planting_kind="tree", x=50, y=50, status="placed",
            species_name_ru="Тестовое дерево", species_citation="Тестовое обоснование",
            grid_spacing_m=6.0, grid_spacing_citation="тест",
        ),
        PlacementPoint(
            id="tree_0002", planting_kind="tree", x=60, y=60, status="no_recommended_species",
            species_name_ru=None, species_citation=None, grid_spacing_m=6.0, grid_spacing_citation="тест",
        ),
    ]
    shrub_points = [
        PlacementPoint(
            id=f"shrub_{i:04d}", planting_kind="shrub", x=50 + i, y=50, status="placed",
            species_name_ru="Тестовый кустарник", species_citation="Тестовое обоснование",
            grid_spacing_m=0.5, grid_spacing_citation="тест",
        )
        for i in range(1, 6)
    ]

    placement_results = {
        "tree": PlacementResult(planting_kind="tree", territory_category="dvorovye", points=tree_points),
        "shrub": PlacementResult(planting_kind="shrub", territory_category="dvorovye", points=shrub_points),
    }

    report = build_report(
        territory_category="dvorovye",
        constraint_results=list(constraint_results.values()),
        placement_results=list(placement_results.values()),
        source_files=["test.dxf"],
    )
    return report, constraint_results, placement_results


def test_render_human_report_creates_all_expected_files(tmp_path):
    report, constraint_results, placement_results = _make_fixture()
    paths = render_human_report(report, constraint_results, placement_results, output_dir=tmp_path, object_label="Тестовый объект")

    assert paths.markdown_path.is_file()
    assert paths.html_path.is_file()
    assert paths.scheme_before_path.is_file()
    assert paths.scheme_after_path.is_file()
    assert paths.scheme_before_path.stat().st_size > 0
    assert paths.scheme_after_path.stat().st_size > 0


def test_human_report_markdown_contains_key_sections(tmp_path):
    report, constraint_results, placement_results = _make_fixture()
    paths = render_human_report(report, constraint_results, placement_results, output_dir=tmp_path, object_label="Тестовый объект")

    text = paths.markdown_path.read_text(encoding="utf-8")
    assert "Тестовый объект" in text
    assert "Сводка" in text
    assert "Схема «до посадки»" in text
    assert "Схема «после размещения»" in text
    assert "Таблица обоснований" in text
    assert "tree_0001" in text  # дерево поимённо
    assert "shrub_0001" not in text  # кустарник — сводкой, не поимённо
    assert "Протокол технического контроля" in text
    assert "Известные упрощения" in text
    assert "Тестовое дерево" in text


def test_human_report_html_contains_key_sections(tmp_path):
    report, constraint_results, placement_results = _make_fixture()
    paths = render_human_report(report, constraint_results, placement_results, output_dir=tmp_path)

    text = paths.html_path.read_text(encoding="utf-8")
    assert "<html" in text
    assert "tree_0001" in text
    assert "scheme_before.png" in text
    assert "scheme_after.png" in text


def test_human_report_reflects_otk_violation_honestly(tmp_path):
    """Не должен скрывать нарушение ОТК — прямое требование проекта к отчётам."""
    from pipeline.otk.boundary_plausibility import BoundaryPlausibilityCheck

    site_boundary = box(0, 0, 100, 100)
    forbidden = box(0, 0, 30, 100)
    allowed = site_boundary.difference(forbidden)
    constraint_results = {
        "tree": ConstraintMapResult(
            planting_kind="tree", site_boundary=site_boundary, allowed_zone=allowed, forbidden_zone=forbidden
        ),
    }
    placement_results = {
        "tree": PlacementResult(planting_kind="tree", territory_category="dvorovye", points=[]),
    }
    bad_plausibility = BoundaryPlausibilityCheck(
        verdict="implausible",
        boundary_area_m2=10000.0,
        features_bbox_area_m2=1.0,
        ratio=10000.0,
        explanation="Тестовая честная жалоба на неправдоподобную границу.",
    )
    report = build_report(
        territory_category="dvorovye",
        constraint_results=list(constraint_results.values()),
        placement_results=list(placement_results.values()),
        source_files=["test.dxf"],
        boundary_plausibility=bad_plausibility,
    )
    paths = render_human_report(report, constraint_results, placement_results, output_dir=tmp_path)
    text = paths.markdown_path.read_text(encoding="utf-8")
    assert "Тестовая честная жалоба" in text
    assert "implausible" in text
