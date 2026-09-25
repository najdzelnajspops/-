"""Тесты политики отступа от существующего сохраняемого насаждения —
явно НЕ норма, см. data/reference/existing_vegetation_buffer_policy.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.existing_vegetation import ExistingVegetationPolicy


def test_known_vigor_tier_returns_documented_distance():
    policy = ExistingVegetationPolicy()

    vigorous = policy.clearance_for_tier("vigorous")
    assert vigorous.distance_m == 6
    assert vigorous.verified is False

    medium = policy.clearance_for_tier("medium")
    assert medium.distance_m == 4

    dwarf = policy.clearance_for_tier("dwarf_columnar")
    assert dwarf.distance_m == 3


def test_unknown_vigor_defaults_to_most_conservative_tier():
    policy = ExistingVegetationPolicy()
    unknown = policy.clearance_for_tier(None)
    vigorous = policy.clearance_for_tier("vigorous")
    assert unknown.distance_m == vigorous.distance_m
    assert unknown.tier == "vigorous"


def test_apply_to_features_sets_conservative_distance_and_keeps_unverified():
    policy = ExistingVegetationPolicy()
    features = [
        ConstraintFeature(id="a", boundary_type="existing_tree_preserve", geometry=Point(0, 0)),
        ConstraintFeature(id="b", boundary_type="gas_pipeline", geometry=Point(1, 1)),
    ]
    updated = policy.apply_to_features(features)

    veg = next(f for f in updated if f.id == "a")
    other = next(f for f in updated if f.id == "b")

    assert veg.explicit_distance_m == 6  # самая консервативная категория (vigorous)
    assert veg.explicit_citation is None  # verified=False по конвенции buffer_engine
    assert veg.label is not None

    # Прочие объекты не тронуты.
    assert other.explicit_distance_m is None
    assert other.explicit_citation is None


if __name__ == "__main__":
    test_known_vigor_tier_returns_documented_distance()
    test_unknown_vigor_defaults_to_most_conservative_tier()
    test_apply_to_features_sets_conservative_distance_and_keeps_unverified()
    print("OK: existing vegetation policy tests passed")
