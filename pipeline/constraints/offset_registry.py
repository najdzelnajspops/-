"""Реестр нормативных отступов дерева/кустарника от сооружений и коммуникаций.

Единственный источник цифр — data/reference/offset_norms.yaml, который сам
пересказывает 743-ПП (Москва), п. 3.6.3, Таблица 3.6.1 (см. docs/NORMATIVE_REFERENCES.md).
Модуль намеренно не хранит числа в коде — только их загрузку и lookup, чтобы
обновление норм не требовало правки кода.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from pipeline.common.schema_validation import require_keys, source_updated_at

PlantingKind = Literal["tree", "shrub"]

DEFAULT_OFFSET_NORMS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "offset_norms.yaml"
)


@dataclass(frozen=True)
class OffsetRule:
    boundary_type: str
    boundary_label_ru: str
    min_distance_tree_m: float | None
    min_distance_shrub_m: float | None

    def min_distance(self, planting_kind: PlantingKind) -> float | None:
        if planting_kind == "tree":
            return self.min_distance_tree_m
        if planting_kind == "shrub":
            return self.min_distance_shrub_m
        raise ValueError(f"Неизвестный тип посадки: {planting_kind!r} (ожидалось 'tree' или 'shrub')")


class OffsetRegistry:
    """Загружает и предоставляет lookup по data/reference/offset_norms.yaml."""

    def __init__(self, path: Path = DEFAULT_OFFSET_NORMS_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        require_keys(raw, {"source": dict, "offsets": list}, source_path=path)

        self.source_act_code: str = raw["source"]["act_code"]
        self.source_act_name: str = raw["source"]["act_name"]
        self.source_status: str = raw["source"]["status"]

        self._rules: dict[str, OffsetRule] = {}
        for entry in raw["offsets"]:
            rule = OffsetRule(
                boundary_type=entry["boundary_type"],
                boundary_label_ru=entry["boundary_label_ru"],
                min_distance_tree_m=entry.get("min_distance_tree_m"),
                min_distance_shrub_m=entry.get("min_distance_shrub_m"),
            )
            self._rules[rule.boundary_type] = rule

        self.planting_density = raw.get("planting_density_recommendations", {})

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def is_known(self, boundary_type: str) -> bool:
        return boundary_type in self._rules

    def get_rule(self, boundary_type: str) -> OffsetRule | None:
        return self._rules.get(boundary_type)

    def min_distance(self, boundary_type: str, planting_kind: PlantingKind) -> float | None:
        """Возвращает минимальный отступ в метрах, либо None если:
        - тип границы неизвестен реестру (см. is_known), или
        - для этого типа насаждения отступ в таблице не задан (например, кустарник от мачты).
        Вызывающий код обязан различать эти два случая явно (см. buffer_engine.py) —
        неизвестный тип границы это не то же самое, что "отступ не требуется".
        """
        rule = self.get_rule(boundary_type)
        if rule is None:
            return None
        return rule.min_distance(planting_kind)

    def known_boundary_types(self) -> list[str]:
        return sorted(self._rules.keys())

    def citation(self, boundary_type: str) -> str | None:
        """Текст обоснования для отчёта интерпретации — только для известных реестру типов."""
        rule = self.get_rule(boundary_type)
        if rule is None:
            return None
        return f"{self.source_act_name} — «{rule.boundary_label_ru}»"
