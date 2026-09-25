"""Рекомендации по видам растений для категорий территорий.

Источник: ППМ №623-ПП (МГСН 1.02-02), Приложение В, Таблица В.6 «Виды растений
в различных категориях насаждений» (см. docs/NORMATIVE_REFERENCES.md).
Извлечено через python-docx из реальной структуры таблицы документа (не из
плоского текста) — 80 строк, 6 столбцов, без риска сдвига ячеек.

ВАЖНО: это ответ на вопрос «какие виды рекомендованы для какого типа территории»
(парки/скверы/улицы/дворы/спецзоны) — НЕ на вопрос «какая глубина/агрессивность
корневой системы» (см. docs/ARCHITECTURE.md §3.1b, docs/OPEN_QUESTIONS.md — это
по-прежнему отдельный, не закрытый источник). Пометки вида "с огр." в самом
документе не объясняют причину ограничения — не домысливать (может быть корни,
может быть пух/аллергенность/ломкость и др.).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "species_territory_recommendations.json"
)

TerritoryCategory = Literal[
    "gardens_parks", "squares_boulevards", "streets_roads", "courtyards", "special_zones"
]
Flag = Literal["recommended", "not_recommended", "unspecified"]


@dataclass(frozen=True)
class CategoryRecommendation:
    flag: Flag
    note: str | None


@dataclass(frozen=True)
class SpeciesEntry:
    name_ru: str
    life_form: str  # tree | shrub | liana
    recommendations: dict[TerritoryCategory, CategoryRecommendation]

    def is_recommended_for(self, category: TerritoryCategory) -> bool:
        return self.recommendations[category].flag == "recommended"

    def has_restriction_for(self, category: TerritoryCategory) -> bool:
        rec = self.recommendations[category]
        return rec.flag == "recommended" and rec.note is not None


class SpeciesTerritoryRegistry:
    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        require_keys(raw, {"source": dict, "species": list}, source_path=path)

        self.source_act_code: str = raw["source"]["act_code"]
        self.source_act_name: str = raw["source"]["act_name"]

        categories: list[TerritoryCategory] = [
            "gardens_parks", "squares_boulevards", "streets_roads", "courtyards", "special_zones"
        ]

        self._by_name: dict[str, SpeciesEntry] = {}
        for item in raw["species"]:
            recs = {
                cat: CategoryRecommendation(flag=item[cat]["flag"], note=item[cat]["note"])
                for cat in categories
            }
            entry = SpeciesEntry(name_ru=item["name_ru"], life_form=item["life_form"], recommendations=recs)
            self._by_name[item["name_ru"].lower()] = entry

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def lookup(self, name_ru: str) -> SpeciesEntry | None:
        return self._by_name.get(name_ru.strip().lower())

    def all_entries(self) -> list[SpeciesEntry]:
        return list(self._by_name.values())

    def recommended_for(self, category: TerritoryCategory, life_form: str | None = None) -> list[SpeciesEntry]:
        return [
            e
            for e in self._by_name.values()
            if e.is_recommended_for(category) and (life_form is None or e.life_form == life_form)
        ]
