"""Проверка предлагаемых к посадке видов на инвазивность.

Источник: ППМ № 369-ПП от 03.03.2026, Приложение 1 (см. docs/NORMATIVE_REFERENCES.md).
Прямой запрет на высадку — Приложение 2, п. 2.4 того же акта.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_INVASIVE_SPECIES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "invasive_species.json"
)


@dataclass(frozen=True)
class InvasiveSpeciesEntry:
    group: str
    name_ru: str
    name_lat: str


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


class InvasiveSpeciesRegistry:
    def __init__(self, path: Path = DEFAULT_INVASIVE_SPECIES_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        require_keys(raw, {"source": dict, "prohibition_clause": str, "species": list}, source_path=path)

        self.source_act_code: str = raw["source"]["act_code"]
        self.source_act_name: str = raw["source"]["act_name"]
        self.prohibition_clause: str = raw["prohibition_clause"]

        self._entries = [
            InvasiveSpeciesEntry(group=e["group"], name_ru=e["name_ru"], name_lat=e["name_lat"])
            for e in raw["species"]
        ]
        # Индекс по нормализованному имени — русскому и латинскому. Латинское имя
        # в реестре иногда включает автора вида (например "L." или "(Mill.) Swingle") —
        # матчим и по полному значению, и по первым двум словам (род + вид), чтобы
        # не требовать от вызывающего кода точного воспроизведения авторства.
        self._by_ru: dict[str, InvasiveSpeciesEntry] = {}
        self._by_lat: dict[str, InvasiveSpeciesEntry] = {}
        for entry in self._entries:
            self._by_ru[_normalize(entry.name_ru)] = entry
            lat_norm = _normalize(entry.name_lat)
            self._by_lat[lat_norm] = entry
            lat_genus_species = " ".join(lat_norm.split()[:2])
            self._by_lat.setdefault(lat_genus_species, entry)

    def check(self, name_ru: str | None = None, name_lat: str | None = None) -> InvasiveSpeciesEntry | None:
        """Возвращает найденную запись реестра, если вид инвазивный, иначе None.
        Матчинг по точному нормализованному совпадению — никаких эвристик/похожести,
        чтобы не давать ни ложных срабатываний, ни ложного спокойствия.
        """
        if name_ru:
            hit = self._by_ru.get(_normalize(name_ru))
            if hit:
                return hit
        if name_lat:
            norm = _normalize(name_lat)
            hit = self._by_lat.get(norm) or self._by_lat.get(" ".join(norm.split()[:2]))
            if hit:
                return hit
        return None

    def is_invasive(self, name_ru: str | None = None, name_lat: str | None = None) -> bool:
        return self.check(name_ru=name_ru, name_lat=name_lat) is not None

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def all_entries(self) -> list[InvasiveSpeciesEntry]:
        return list(self._entries)
