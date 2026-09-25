"""Оценочная стоимость посадочного материала по категориям.

См. data/reference/planting_cost_estimates.yaml — единственный источник чисел,
явно ОБЯЗАН быть редактируемым человеком (прямое требование пользователя,
2026-09-16): код здесь только читает и классифицирует, ничего не хардкодит.

НЕ норма — оценка по реальным розничным/мелкооптовым прайс-листам нескольких
российских питомников (см. источники и оговорки в самом YAML-файле). Организаторы
хакатона подтвердили устно (не документом), что фактическая стоимость «плавает»
в зависимости от питомника-подрядчика — отсюда требование редактируемости.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesEntry
from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "planting_cost_estimates.yaml"
)

SizeClass = Literal["standard", "large"]


@dataclass(frozen=True)
class CostCategory:
    key: str
    label_ru: str
    min_rub: float
    max_rub: float
    default_rub: float
    note: str


def classify_cost_category(entry: DpioosSpeciesEntry, size: SizeClass = "standard") -> str:
    """Определяет ключ категории стоимости для вида. `size` — размерный класс
    посадочного материала («standard»/«large», соответствует 515-ПП «IV группа»/
    «крупномер»). Placement Generator сейчас не выбирает размер посадочного
    материала для конкретной точки — по умолчанию используется "standard"
    (осознанное упрощение, не гадание: явно самая частая, а не самая дорогая
    оценка, см. docs/OPEN_QUESTIONS.md)."""
    group = entry.life_form_group_ru.lower()
    if "лиан" in group:
        return "liana"

    is_conifer = "хвойн" in group

    if entry.life_form == "tree":
        return f"{'conifer' if is_conifer else 'deciduous'}_tree_{size}"
    if entry.life_form == "shrub":
        if is_conifer:
            return "conifer_shrub"  # единая категория, без разбивки по размеру (см. YAML)
        return f"deciduous_shrub_{size}"
    raise ValueError(f"Неизвестная жизненная форма: {entry.life_form!r}")


class PlantingCostCatalog:
    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        require_keys(raw, {"target_budget_per_hectare": dict, "categories": dict}, source_path=path)

        self.override_note: str = raw.get("override_note", "")
        self.target_budget_per_hectare_min_rub: float = raw["target_budget_per_hectare"]["min_rub"]
        self.target_budget_per_hectare_max_rub: float = raw["target_budget_per_hectare"]["max_rub"]

        self._categories: dict[str, CostCategory] = {}
        for key, item in raw["categories"].items():
            self._categories[key] = CostCategory(
                key=key,
                label_ru=item["label_ru"],
                min_rub=item["min_rub"],
                max_rub=item["max_rub"],
                default_rub=item["default_rub"],
                note=item.get("note", ""),
            )

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def category(self, key: str) -> CostCategory:
        return self._categories[key]

    def default_cost_for(self, entry: DpioosSpeciesEntry, size: SizeClass = "standard") -> float:
        category_key = classify_cost_category(entry, size)
        return self._categories[category_key].default_rub

    def category_for(self, entry: DpioosSpeciesEntry, size: SizeClass = "standard") -> CostCategory:
        return self._categories[classify_cost_category(entry, size)]
