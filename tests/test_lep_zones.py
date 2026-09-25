"""Тесты охранных зон ЛЭП (Постановление Правительства РФ № 160) —
отдельная от 743-ПП норма, интегрируется в Constraint Engine через
ConstraintFeature.explicit_distance_m (см. pipeline/constraints/buffer_engine.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from shapely.geometry import LineString, Point, Polygon

from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.constraints.offset_registry import OffsetRegistry


def test_overhead_line_zone_width_by_voltage_tier():
    lep = LepZoneRegistry()

    assert lep.overhead_line_zone_width_m(0.4) == 2.0     # до 1 кВ
    assert lep.overhead_line_zone_width_m(10) == 10.0      # 1-20 кВ
    assert lep.overhead_line_zone_width_m(35) == 15.0
    assert lep.overhead_line_zone_width_m(110) == 20.0
    assert lep.overhead_line_zone_width_m(220) == 25.0
    assert lep.overhead_line_zone_width_m(500) == 30.0
    assert lep.overhead_line_zone_width_m(750) == 40.0
    assert lep.overhead_line_zone_width_m(1150) == 55.0


def test_overhead_line_above_known_tariff_raises_instead_of_guessing():
    lep = LepZoneRegistry()
    with pytest.raises(ValueError):
        lep.overhead_line_zone_width_m(1500)


def test_lep_zone_integrates_into_constraint_map_as_verified():
    """110 кВ ЛЭП пересекает участок — зона 20 м должна быть шире, чем 743-ПП
    дал бы для обычной мачты освещения (4 м) — это отдельная, более строгая норма."""
    site = Polygon([(0, 0), (60, 0), (60, 40), (0, 40)])
    lep_line = LineString([(30, 0), (30, 40)])

    lep_registry = LepZoneRegistry()
    width = lep_registry.overhead_line_zone_width_m(voltage_kv=110)

    feature = ConstraintFeature(
        id="lep-1",
        boundary_type="power_line_overhead_110kv",
        geometry=lep_line,
        label="ЛЭП 110 кВ",
        explicit_distance_m=width,
        explicit_citation=lep_registry.citation(),
    )

    offset_registry = OffsetRegistry()
    result = build_constraint_map(
        site_boundary=site,
        features=[feature],
        planting_kind="tree",
        registry=offset_registry,
    )

    assert len(result.zone_sources) == 1
    zone = result.zone_sources[0]
    assert zone.verified is True
    assert zone.distance_m == 20.0
    assert "№ 160" in zone.citation or "160" in zone.citation

    # Точка в 10 м от линии — внутри охранной зоны 20 м, запрещена.
    assert result.allowed_zone.contains(Point(20, 20)) is False
    # Точка в 25 м — уже за пределами зоны.
    assert result.allowed_zone.contains(Point(5, 20)) is True


if __name__ == "__main__":
    test_overhead_line_zone_width_by_voltage_tier()
    test_overhead_line_above_known_tariff_raises_instead_of_guessing()
    test_lep_zone_integrates_into_constraint_map_as_verified()
    print("OK: LEP zone tests passed")
