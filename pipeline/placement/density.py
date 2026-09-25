"""Максимальная плотность посадки (шт/га) — 623-ПП (МГСН 1.02-02), Таблица В.1.

См. data/reference/planting_density_per_hectare.yaml — единственный источник
чисел. Найдено и внедрено 2026-09-16 взамен чисто геометрического расчёта
густоты по шагу сетки (см. pipeline/placement/generator.py, докстринг модуля,
и docs/SESSION_LOG.md про баг с 292932 кустами на одном объекте)."""

from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.catalog.dpioos_species_catalog import LifeForm, TerritoryCategory
from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "planting_density_per_hectare.yaml"
)


class PlantingDensityRegistry:
    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        require_keys(raw, {"density_per_ha": dict}, source_path=path)
        self._data: dict = raw["density_per_ha"]

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def _range_per_ha(self, territory_category: TerritoryCategory, life_form: LifeForm) -> tuple[float, float]:
        entry = self._data[territory_category]
        key = "trees" if life_form == "tree" else "shrubs"
        lo, hi = entry[key]
        return float(lo), float(hi)

    def target_count_for_area(
        self, territory_category: TerritoryCategory, life_form: LifeForm, area_m2: float
    ) -> int:
        """Целевое число посадок на площадь area_m2 — середина диапазона норм
        (не максимум: максимум — жёсткий предел, не цель по умолчанию)."""
        lo, hi = self._range_per_ha(territory_category, life_form)
        area_ha = area_m2 / 10_000
        return max(1, round((lo + hi) / 2 * area_ha))

    def max_count_for_area(
        self, territory_category: TerritoryCategory, life_form: LifeForm, area_m2: float
    ) -> int:
        """Жёсткий верхний предел (максимум нормы) — для проверки, что итоговое
        число посадок не превышает 623-ПП, Таблица В.1."""
        _, hi = self._range_per_ha(territory_category, life_form)
        area_ha = area_m2 / 10_000
        return max(1, round(hi * area_ha))

    def min_count_for_area(
        self, territory_category: TerritoryCategory, life_form: LifeForm, area_m2: float
    ) -> int:
        """Нижняя граница диапазона нормы — для проверки, что предложенное
        человеком число посадок не занижено относительно 623-ПП, Таблица В.1
        (см. pipeline/review/placement_review.py, проверка человеческого выбора)."""
        lo, _ = self._range_per_ha(territory_category, life_form)
        area_ha = area_m2 / 10_000
        return max(0, round(lo * area_ha))
