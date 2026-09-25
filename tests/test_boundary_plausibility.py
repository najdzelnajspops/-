"""Тесты агента ОТК v1 — проверка правдоподобия границы участка
(pipeline/otk/boundary_plausibility.py).

Ключевой сценарий — реальный случай 2026-09-16 (docs/DATA_STRUCTURE.md §11.3):
Ingest мог вернуть site_boundary_status="found" с площадью на порядки меньше
реального охвата объектов-коммуникаций участка. Эта проверка обязана поймать
такой случай независимо, не полагаясь на статус самого Ingest.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import LineString, Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.otk.boundary_plausibility import check_boundary_plausibility


def _make_features(n: int, spread: float) -> list[ConstraintFeature]:
    return [
        ConstraintFeature(id=f"f{i}", boundary_type="gas_pipeline", geometry=LineString([(0, i), (spread, i)]))
        for i in range(n)
    ]


def test_plausible_boundary_matching_features_extent_passes():
    boundary = box(0, 0, 500, 500)
    features = _make_features(10, spread=500)  # bbox коммуникаций ~500x500 — сопоставимо с границей
    result = check_boundary_plausibility(boundary, features)
    assert result.verdict == "ok"
    assert result.ratio is not None and result.ratio > 0.5


def test_implausibly_tiny_boundary_relative_to_features_is_flagged():
    """Реальный сценарий бага: граница — крошечный полигон (побочный продукт
    сбойного polygonize()), а коммуникации разбросаны на большой площади."""
    boundary = box(0, 0, 7, 7)  # ~49 м², как реальный дефектный случай
    features = _make_features(50, spread=1000)  # bbox коммуникаций ~1000x1000 м
    result = check_boundary_plausibility(boundary, features)
    assert result.verdict == "implausible"
    assert "подозрительно мала" in result.explanation


def test_no_features_is_not_checked_not_silently_ok():
    boundary = box(0, 0, 100, 100)
    result = check_boundary_plausibility(boundary, [])
    assert result.verdict == "not_checked"


def test_no_boundary_is_not_checked():
    features = _make_features(5, spread=100)
    result = check_boundary_plausibility(None, features)
    assert result.verdict == "not_checked"


def test_single_point_feature_does_not_crash_on_zero_area_bbox():
    """Если все объекты-коммуникации — точки на одной прямой/в одной точке,
    bbox может выродиться в нулевую площадь — не должно падать с делением на 0."""
    boundary = box(0, 0, 10, 10)
    features = [ConstraintFeature(id="p1", boundary_type="existing_tree_preserve", geometry=Point(5, 5))]
    result = check_boundary_plausibility(boundary, features)
    assert result.ratio is None
    assert result.verdict in ("ok", "not_checked")


if __name__ == "__main__":
    test_plausible_boundary_matching_features_extent_passes()
    test_implausibly_tiny_boundary_relative_to_features_is_flagged()
    test_no_features_is_not_checked_not_silently_ok()
    test_no_boundary_is_not_checked()
    test_single_point_feature_does_not_crash_on_zero_area_bbox()
    print("OK: boundary plausibility (Agent OTK v1) tests passed")
