"""Тесты реестра рекомендаций по территориям (623-ПП, Таблица В.6)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.species_territory_recommendations import SpeciesTerritoryRegistry


def test_registry_loads_all_74_species():
    registry = SpeciesTerritoryRegistry()
    entries = registry.all_entries()
    assert len(entries) == 74
    assert sum(1 for e in entries if e.life_form == "tree") == 49
    assert sum(1 for e in entries if e.life_form == "shrub") == 24
    assert sum(1 for e in entries if e.life_form == "liana") == 1


def test_willow_lomkaya_restricted_on_streets_and_courtyards():
    """Ива ломкая — известна агрессивной корневой системой (см. обсуждение
    в docs/OPEN_QUESTIONS.md), в документе просто помечена как не рекомендованная
    для улиц/дворов/спецзон — без объяснения причины, но сигнал согласуется."""
    registry = SpeciesTerritoryRegistry()
    willow = registry.lookup("Ива ломкая")
    assert willow is not None
    assert willow.recommendations["streets_roads"].flag == "not_recommended"
    assert willow.recommendations["courtyards"].flag == "not_recommended"


def test_pufyreplodnik_matches_invasive_species_signal():
    """Пузыреплодник калинолистный — числится инвазивным (369-ПП, группа III,
    см. pipeline/catalog/invasive_species.py) И одновременно не рекомендован
    для парков/скверов/улиц по 623-ПП — независимые источники, согласованный
    сигнал, не противоречат друг другу."""
    registry = SpeciesTerritoryRegistry()
    entry = registry.lookup("Пузыреплодник калинолистный")
    assert entry is not None
    assert entry.recommendations["gardens_parks"].flag == "not_recommended"
    assert entry.recommendations["streets_roads"].flag == "not_recommended"


def test_recommended_for_returns_only_matching_flag():
    registry = SpeciesTerritoryRegistry()
    street_trees = registry.recommended_for("streets_roads", life_form="tree")
    names = {e.name_ru for e in street_trees}
    assert "Вяз гладкий" in names  # явный "+" без оговорок
    assert "Ива ломкая" not in names  # "-"


if __name__ == "__main__":
    test_registry_loads_all_74_species()
    test_willow_lomkaya_restricted_on_streets_and_courtyards()
    test_pufyreplodnik_matches_invasive_species_signal()
    test_recommended_for_returns_only_matching_flag()
    print("OK: species territory recommendations tests passed")
