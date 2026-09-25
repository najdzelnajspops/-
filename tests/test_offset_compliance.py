"""Тесты Агента ОТК, проверка 6.1 (продолжение) — независимая сверка отступов
(pipeline/otk/offset_compliance.py).

Ключевой сценарий: даже если Constraint Engine (build_constraint_map) сгенерировал
allowed_zone с багом (например, перепутал знак буфера или объединил не те
геометрии), эта проверка не должна унаследовать ту же ошибку — она пересчитывает
расстояние точка-объект заново по сырой (небуферизованной) геометрии, поэтому
тесты намеренно НЕ используют build_constraint_map/generate_placement, а строят
PlacementPoint вручную — в том числе с координатами, которые нарушают отступ,
как если бы это была "утечка" сквозь баг генератора.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.otk.offset_compliance import check_offset_compliance
from pipeline.placement.generator import PlacementPoint

REGISTRY = OffsetRegistry()  # building_wall: tree=5.0 m, shrub=1.5 m (743-ПП, Таблица 3.6.1)


def _point(id_: str, x: float, y: float, kind: str = "tree") -> PlacementPoint:
    return PlacementPoint(
        id=id_,
        planting_kind=kind,
        x=x,
        y=y,
        status="placed",
        species_name_ru="Тестовый вид",
        species_citation="Тестовое обоснование",
        grid_spacing_m=6.0,
        grid_spacing_citation="тест",
    )


def test_point_far_enough_from_building_passes():
    boundary = box(0, 0, 100, 100)
    building = LineString([(10, 0), (10, 100)])  # стена вдоль x=10
    features = [ConstraintFeature(id="wall1", boundary_type="building_wall", geometry=building)]
    points = [_point("tree_0001", 16.0, 50.0)]  # 6 м от стены, требуется 5 м

    result = check_offset_compliance(boundary, features, REGISTRY, "tree", points)
    assert result.verdict == "ok"
    assert result.violations == []
    assert result.points_checked == 1


def test_point_too_close_to_building_is_flagged():
    """Смоделированная утечка бага Constraint Engine: точка оказалась ближе
    требуемого отступа к стене здания — независимая проверка обязана поймать
    это, даже если сам генератор (гипотетически) ошибочно посчитал бы её
    допустимой через сломанный allowed_zone."""
    boundary = box(0, 0, 100, 100)
    building = LineString([(10, 0), (10, 100)])
    features = [ConstraintFeature(id="wall1", boundary_type="building_wall", geometry=building)]
    points = [_point("tree_0001", 12.0, 50.0)]  # 2 м от стены, требуется 5 м

    result = check_offset_compliance(boundary, features, REGISTRY, "tree", points)
    assert result.verdict == "violation"
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.kind == "insufficient_offset"
    assert violation.feature_id == "wall1"
    assert violation.required_distance_m == 5.0
    assert abs(violation.actual_distance_m - 2.0) < 1e-6


def test_different_offset_per_planting_kind():
    """Тот же объект и та же точка — для кустарника (отступ 1.5 м) допустимо,
    для дерева (отступ 5.0 м) то же расстояние 2 м — нарушение."""
    boundary = box(0, 0, 100, 100)
    building = LineString([(10, 0), (10, 100)])
    features = [ConstraintFeature(id="wall1", boundary_type="building_wall", geometry=building)]
    points = [_point("shrub_0001", 12.0, 50.0, kind="shrub")]  # 2 м от стены

    result = check_offset_compliance(boundary, features, REGISTRY, "shrub", points)
    assert result.verdict == "ok"


def test_point_outside_site_boundary_is_flagged_independently_of_allowed_zone():
    """Точка вне границы участка — должно ловиться напрямую по site_boundary,
    не через ConstraintMapResult.allowed_zone (эта проверка его вообще не видит)."""
    boundary = box(0, 0, 100, 100)
    points = [_point("tree_0001", 150.0, 150.0)]  # далеко за пределами границы

    result = check_offset_compliance(boundary, [], REGISTRY, "tree", points)
    assert result.verdict == "violation"
    assert any(v.kind == "outside_site_boundary" for v in result.violations)


def test_unknown_boundary_type_is_skipped_not_silently_flagged():
    """Тип границы, которого нет в реестре норм — как и в build_constraint_map,
    не проверяется (нет нормы, с которой сравнивать), а не молча считается
    нарушением или молча считается допустимым без объяснения."""
    boundary = box(0, 0, 100, 100)
    features = [
        ConstraintFeature(id="mystery1", boundary_type="unknown_type_xyz", geometry=LineString([(10, 0), (10, 100)]))
    ]
    points = [_point("tree_0001", 10.5, 50.0)]  # 0.5 м от объекта неизвестного типа

    result = check_offset_compliance(boundary, features, REGISTRY, "tree", points)
    assert result.verdict == "ok"


def test_no_points_of_this_kind_is_not_checked():
    boundary = box(0, 0, 100, 100)
    result = check_offset_compliance(boundary, [], REGISTRY, "shrub", [_point("tree_0001", 50.0, 50.0, kind="tree")])
    assert result.verdict == "not_checked"
    assert result.points_checked == 0


def test_explicit_distance_feature_used_directly():
    """ConstraintFeature.explicit_distance_m (например, охранная зона ЛЭП с
    известным классом напряжения) должен использоваться напрямую, минуя
    OffsetRegistry — так же, как это делает build_constraint_map."""
    boundary = box(0, 0, 100, 100)
    features = [
        ConstraintFeature(
            id="lep1",
            boundary_type="lep_overhead",
            geometry=LineString([(10, 0), (10, 100)]),
            explicit_distance_m=20.0,
            explicit_citation="ПУЭ, охранная зона ВЛ 110 кВ",
        )
    ]
    points = [_point("tree_0001", 15.0, 50.0)]  # 5 м от ЛЭП, требуется 20 м

    result = check_offset_compliance(boundary, features, REGISTRY, "tree", points)
    assert result.verdict == "violation"
    assert result.violations[0].required_distance_m == 20.0


if __name__ == "__main__":
    test_point_far_enough_from_building_passes()
    test_point_too_close_to_building_is_flagged()
    test_different_offset_per_planting_kind()
    test_point_outside_site_boundary_is_flagged_independently_of_allowed_zone()
    test_unknown_boundary_type_is_skipped_not_silently_flagged()
    test_no_points_of_this_kind_is_not_checked()
    test_explicit_distance_feature_used_directly()
    print("OK: offset compliance (Agent OTK 6.1) tests passed")
