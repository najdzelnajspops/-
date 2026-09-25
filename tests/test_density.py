"""Тесты нормы плотности посадки (pipeline/placement/density.py, 623-ПП Таблица В.1)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.placement.density import PlantingDensityRegistry


def test_dvorovye_matches_623pp_table_v1():
    registry = PlantingDensityRegistry()
    # 1 га ровно, dvorovye: деревья 100-120, кустарники 400-480 шт/га
    assert registry.target_count_for_area("dvorovye", "tree", area_m2=10_000) == 110
    assert registry.target_count_for_area("dvorovye", "shrub", area_m2=10_000) == 440
    assert registry.max_count_for_area("dvorovye", "tree", area_m2=10_000) == 120
    assert registry.max_count_for_area("dvorovye", "shrub", area_m2=10_000) == 480


def test_real_object_scale_matches_expectation():
    """Регрессионный сценарий 2026-09-16: 49,4 га, dvorovye — раньше давало
    292932 куста, норма ограничивает разумным числом."""
    registry = PlantingDensityRegistry()
    area_m2 = 494_315.83
    max_shrubs = registry.max_count_for_area("dvorovye", "shrub", area_m2)
    assert max_shrubs < 25_000  # верхний предел нормы, не сотни тысяч
    assert max_shrubs > 15_000  # но и не искусственно занижен


def test_all_eight_territory_categories_present():
    registry = PlantingDensityRegistry()
    for category in [
        "dvorovye", "doshkolnye", "obscheobr", "zdravoohr",
        "magistrali", "ploschadi", "parki", "proizvodstvennye",
    ]:
        count = registry.target_count_for_area(category, "shrub", area_m2=10_000)
        assert count > 0


if __name__ == "__main__":
    test_dvorovye_matches_623pp_table_v1()
    test_real_object_scale_matches_expectation()
    test_all_eight_territory_categories_present()
    print("OK: density registry tests passed")
