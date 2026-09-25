"""Тесты каталога ДПиООС (основной ассортимент + перспективные виды)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog, species_conflict_reason


def test_catalog_loads_and_classifies_life_forms():
    catalog = DpioosSpeciesCatalog()
    entries = catalog.all_entries()
    assert len(entries) > 300  # 329 сырых строк минус лианы (life_form=None -> отфильтрованы)
    assert all(e.life_form in ("tree", "shrub") for e in entries)


def test_yel_kolyuchaya_recommended_everywhere():
    """Ель колючая — первая строка «основного ассортимента», все 8 категорий '+'."""
    catalog = DpioosSpeciesCatalog()
    hits = catalog.lookup("Ель колючая (формы и сорта)")
    assert len(hits) == 1
    entry = hits[0]
    assert entry.life_form == "tree"
    for category in catalog.territory_categories:
        assert entry.is_recommended_for(category)


def test_recommended_for_filters_by_flag_and_life_form():
    catalog = DpioosSpeciesCatalog()
    trees = catalog.recommended_for("dvorovye", life_form="tree")
    shrubs = catalog.recommended_for("dvorovye", life_form="shrub")
    assert len(trees) > 0
    assert len(shrubs) > 0
    assert all(e.life_form == "tree" for e in trees)
    assert all(e.life_form == "shrub" for e in shrubs)


def test_citation_includes_footnote_text():
    catalog = DpioosSpeciesCatalog()
    boyaryshnik = next(e for e in catalog.all_entries() if e.name_ru.startswith("Боярышник Арнольда") and 2 in e.footnotes)
    citation = catalog.citation_for(boyaryshnik, "dvorovye")
    assert "Боярышник Арнольда" in citation
    assert "детских и спортивных площадок" in citation


def test_juniper_conflicts_with_apple_pear_quince_but_not_unrelated_species():
    """Сноска [6]: можжевельник (переносчик ржавчинных грибов) — конфликт с
    яблоней/грушей/айвой (восприимчивый хозяин), симметрично; с несвязанным
    видом (ель) — конфликта нет."""
    catalog = DpioosSpeciesCatalog()
    juniper = next(e for e in catalog.all_entries() if e.is_rust_fungus_alternate_host())
    fruit = next(e for e in catalog.all_entries() if e.is_rust_susceptible_fruit_host())
    spruce = next(e for e in catalog.all_entries() if e.name_ru.startswith("Ель"))

    assert species_conflict_reason(juniper, fruit) is not None
    assert species_conflict_reason(fruit, juniper) is not None  # симметрично
    assert species_conflict_reason(juniper, spruce) is None
    assert species_conflict_reason(fruit, spruce) is None


if __name__ == "__main__":
    test_catalog_loads_and_classifies_life_forms()
    test_yel_kolyuchaya_recommended_everywhere()
    test_recommended_for_filters_by_flag_and_life_form()
    test_citation_includes_footnote_text()
    test_juniper_conflicts_with_apple_pear_quince_but_not_unrelated_species()
    print("OK: dpioos species catalog tests passed")
