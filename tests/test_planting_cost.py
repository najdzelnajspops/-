"""Тесты оценочной стоимости посадочного материала (pipeline/catalog/planting_cost.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.planting_cost import PlantingCostCatalog, classify_cost_category


def test_classify_cost_category_for_real_species_from_catalog():
    catalog = DpioosSpeciesCatalog()
    spruce = next(e for e in catalog.all_entries() if e.name_ru.startswith("Ель") and e.life_form == "tree")
    juniper = next(e for e in catalog.all_entries() if e.name_ru.startswith("Можжевельник") and e.life_form == "shrub")
    birch = next(e for e in catalog.all_entries() if e.name_ru.startswith("Берез") and e.life_form == "tree")

    assert classify_cost_category(spruce) == "conifer_tree_standard"
    assert classify_cost_category(spruce, size="large") == "conifer_tree_large"
    assert classify_cost_category(juniper) == "conifer_shrub"
    assert classify_cost_category(birch) == "deciduous_tree_standard"


def test_cost_catalog_loads_all_categories_and_returns_positive_defaults():
    cost_catalog = PlantingCostCatalog()
    catalog = DpioosSpeciesCatalog()
    for entry in catalog.all_entries()[:20]:
        cost = cost_catalog.default_cost_for(entry)
        assert cost > 0


def test_target_budget_per_hectare_matches_user_specified_range():
    cost_catalog = PlantingCostCatalog()
    assert cost_catalog.target_budget_per_hectare_min_rub == 10_000_000
    assert cost_catalog.target_budget_per_hectare_max_rub == 25_000_000


def test_category_values_are_editable_by_reading_the_yaml_file_directly(tmp_path):
    """Регрессионный тест на требование пользователя: значения обязаны читаться
    из файла, не быть захардкожены в коде — правка YAML обязана менять результат."""
    import yaml as yaml_module

    default_path = Path(__file__).resolve().parents[1] / "data" / "reference" / "planting_cost_estimates.yaml"
    with open(default_path, encoding="utf-8") as f:
        raw = yaml_module.safe_load(f)
    raw["categories"]["conifer_tree_standard"]["default_rub"] = 999999
    custom_path = tmp_path / "custom_costs.yaml"
    with open(custom_path, "w", encoding="utf-8") as f:
        yaml_module.dump(raw, f, allow_unicode=True)

    catalog = DpioosSpeciesCatalog()
    spruce = next(e for e in catalog.all_entries() if e.name_ru.startswith("Ель") and e.life_form == "tree")

    custom_cost_catalog = PlantingCostCatalog(path=custom_path)
    assert custom_cost_catalog.default_cost_for(spruce) == 999999


if __name__ == "__main__":
    test_classify_cost_category_for_real_species_from_catalog()
    test_cost_catalog_loads_all_categories_and_returns_positive_defaults()
    test_target_budget_per_hectare_matches_user_specified_range()
    print("OK: planting cost tests passed (except tmp_path test, run via pytest)")
