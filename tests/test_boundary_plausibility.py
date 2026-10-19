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


def test_boundary_not_overlapping_features_is_flagged_as_misaligned():
    """Граница и коммуникации в разных системах координат: площадь правдоподобна, но участок не
    пересекается ни с одним объектом — раньше проверка отвечала «ok» (найдено 2026-10-19, «Харьковская улица»)."""
    features = _make_features(60, 100.0)  # объекты в x=0..100, y=0..59
    far_boundary = box(1000, 1000, 1100, 1100)
    check = check_boundary_plausibility(far_boundary, features)
    assert check.verdict == "implausible"
    assert "не пересека" in check.explanation


def _make_spread_features(n: int, size: float) -> list[ConstraintFeature]:
    """n коротких объектов, равномерно по квадрату size x size."""
    step = size / max(int(n ** 0.5), 1)
    cols = max(int(n ** 0.5), 1)
    return [
        ConstraintFeature(
            id=f"s{i}",
            boundary_type="gas_pipeline",
            geometry=LineString([((i % cols) * step, (i // cols) * step), ((i % cols) * step + 1, (i // cols) * step)]),
        )
        for i in range(n)
    ]


def test_few_far_outliers_do_not_make_a_correct_boundary_implausible():
    """Несколько далёких выбросов (например, заблудившиеся объекты в нулевой точке чертежа) раздували охват
    всех объектов и давали ложное «implausible» на корректной границе (найдено 2026-10-19: «Кустанайская улица»,
    «Грузинская М ул»). Охват считается без 0,5 % самых далёких объектов с каждой стороны."""
    features = _make_spread_features(2000, 400.0)
    for i in range(8):  # 8 из 2008 — меньше 0,5 % с каждой стороны
        features.append(ConstraintFeature(id=f"out{i}", boundary_type="gas_pipeline", geometry=LineString([(-30000, -30000 + i), (-29999, -30000 + i)])))
    boundary = box(0, 0, 400, 400)  # граница лежит на объектах, площадь 160000
    check = check_boundary_plausibility(boundary, features)
    assert check.verdict == "ok"


def test_tiny_boundary_among_many_features_is_still_flagged():
    """Усечение выбросов не должно скрывать настоящую ошибку: крошечная граница среди тысяч объектов."""
    features = _make_spread_features(2000, 1000.0)
    tiny = box(10, 10, 15, 15)  # 25 кв. м против ~1 000 000 охвата
    assert check_boundary_plausibility(tiny, features).verdict == "implausible"


def test_boundary_overlapping_features_still_ok():
    features = _make_features(60, 100.0)
    near_boundary = box(-10, -10, 120, 80)
    assert check_boundary_plausibility(near_boundary, features).verdict == "ok"


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
