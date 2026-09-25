"""Тесты политики «красные линии — зона запрета размещения насаждений»
(решение пользователя, 2026-09-17, не норма 743-ПП) — pipeline/constraints/red_lines_policy.py."""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.constraints.red_lines_policy import RED_LINES_BOUNDARY_TYPE, apply_red_lines_policy


def test_apply_to_features_sets_zero_distance_and_keeps_unverified():
    red_line_geom = box(0, 0, 10, 2)
    features = [
        ConstraintFeature(id="rl1", boundary_type=RED_LINES_BOUNDARY_TYPE, geometry=red_line_geom),
        ConstraintFeature(id="w1", boundary_type="gas_pipeline", geometry=Point(5, 5)),
    ]
    updated = apply_red_lines_policy(features)

    red_line = next(f for f in updated if f.id == "rl1")
    other = next(f for f in updated if f.id == "w1")

    assert red_line.explicit_distance_m == 0.0
    assert red_line.explicit_citation is None  # verified=False по конвенции buffer_engine
    assert red_line.label is not None

    # Прочие объекты не тронуты.
    assert other.explicit_distance_m is None
    assert other.explicit_citation is None


def test_zero_distance_forbids_exactly_the_drawn_area_not_more_not_less():
    """buffer(0) на уже полигональной геометрии — no-op: запрещённой становится
    РОВНО нарисованная площадь красной линии, без дополнительной полосы вокруг."""
    site_boundary = box(0, 0, 20, 20)
    red_line_geom = box(0, 0, 20, 3)  # полоса вдоль всей нижней границы участка
    features = apply_red_lines_policy(
        [ConstraintFeature(id="rl1", boundary_type=RED_LINES_BOUNDARY_TYPE, geometry=red_line_geom)]
    )

    result = build_constraint_map(
        site_boundary=site_boundary,
        features=features,
        planting_kind="tree",
        registry=OffsetRegistry(),
    )

    assert result.forbidden_zone.equals(red_line_geom)
    assert result.allowed_zone.equals(site_boundary.difference(red_line_geom))
    zone_source = result.zone_sources[0]
    assert zone_source.verified is False
    assert zone_source.distance_m == 0.0


def test_applies_equally_to_tree_and_shrub():
    """Запрет одинаков для дерева и кустарника — не дифференцирован по виду
    насаждения (в отличие от 743-ПП, где у дерева и куста разные отступы)."""
    site_boundary = box(0, 0, 20, 20)
    red_line_geom = box(0, 0, 20, 3)
    features = apply_red_lines_policy(
        [ConstraintFeature(id="rl1", boundary_type=RED_LINES_BOUNDARY_TYPE, geometry=red_line_geom)]
    )
    registry = OffsetRegistry()

    tree_result = build_constraint_map(site_boundary, features, "tree", registry)
    shrub_result = build_constraint_map(site_boundary, features, "shrub", registry)

    assert tree_result.forbidden_zone.equals(shrub_result.forbidden_zone)


def test_holes_and_untouched_geometry_type_polygon():
    """Политика не трогает геометрию сущности — только явный отступ/цитату."""
    poly = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    features = apply_red_lines_policy(
        [ConstraintFeature(id="rl1", boundary_type=RED_LINES_BOUNDARY_TYPE, geometry=poly)]
    )
    assert features[0].geometry.equals(poly)


if __name__ == "__main__":
    test_apply_to_features_sets_zero_distance_and_keeps_unverified()
    test_zero_distance_forbids_exactly_the_drawn_area_not_more_not_less()
    test_applies_equally_to_tree_and_shrub()
    test_holes_and_untouched_geometry_type_polygon()
    print("OK: red lines policy tests passed")
