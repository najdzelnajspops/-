"""Классификация имени слоя DXF в boundary_type для Constraint Engine.

Реальная конвенция имён слоёв (см. data/reference/layer_name_aliases.yaml,
docs/DATA_STRUCTURE.md §9): "<источник>|<категория>". Матчим по части после
ПОСЛЕДНЕГО "|" — если "|" в имени нет, используем имя целиком.

Принцип (см. AGENTS.md): нераспознанный слой — это статус `unclassified`,
а не тихий пропуск и не догадка. Вызывающий код (Territory Onboarding Agent)
обязан показать список unclassified слоёв оператору, а не спрятать их.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_ALIASES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "layer_name_aliases.yaml"
)

ClassificationStatus = Literal[
    "mapped", "lep_unknown_voltage", "ignored", "site_boundary_candidate", "unclassified"
]


@dataclass(frozen=True)
class LayerClassification:
    raw_layer_name: str
    category: str  # часть после последнего "|", очищенная
    status: ClassificationStatus
    boundary_type: str | None = None


class LayerClassifier:
    def __init__(self, path: Path = DEFAULT_ALIASES_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        # Все секции здесь опциональны (код читает через .get(...) с дефолтами
        # ниже) — единственное, что стоит защитить, это грубая порча файла
        # (не словарь верхнего уровня вовсе).
        require_keys(raw, {}, source_path=path)

        self._mapping: dict[str, str] = {}
        for section in ("communication_layers", "topo_layers"):
            self._mapping.update(raw.get(section, {}))

        self._lep_categories: set[str] = set(raw.get("lep_layers", {}).keys())
        self._ignored: set[str] = set(raw.get("ignored_layers", []))
        self.site_boundary_layer_priority: list[str] = list(raw.get("site_boundary_layers", []))

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    @staticmethod
    def _extract_category(raw_layer_name: str) -> str:
        # Часть после ПОСЛЕДНЕГО "|" — источники сами могут содержать "|" в имени.
        category = raw_layer_name.rsplit("|", 1)[-1]
        return category.strip()

    def classify(self, raw_layer_name: str) -> LayerClassification:
        category = self._extract_category(raw_layer_name)

        if category in self._ignored:
            return LayerClassification(raw_layer_name, category, status="ignored")

        if category in self._lep_categories:
            return LayerClassification(
                raw_layer_name, category, status="lep_unknown_voltage", boundary_type=None
            )

        boundary_type = self._mapping.get(category)
        if boundary_type is not None:
            return LayerClassification(raw_layer_name, category, status="mapped", boundary_type=boundary_type)

        if category in self.site_boundary_layer_priority:
            # Не "unclassified" — это осмысленно узнанный слой границы участка,
            # обрабатывается отдельным путём (_find_site_boundary_in_doc), а не
            # как объект-ограничение. Иначе он засорял бы отчёт об
            # unclassified-слоях, будто мы его не понимаем.
            return LayerClassification(raw_layer_name, category, status="site_boundary_candidate")

        return LayerClassification(raw_layer_name, category, status="unclassified")

    def is_site_boundary_category(self, raw_layer_name: str) -> bool:
        return self._extract_category(raw_layer_name) in self.site_boundary_layer_priority

    def site_boundary_priority_rank(self, raw_layer_name: str) -> int | None:
        """Меньше — выше приоритет; None если слой не кандидат на границу участка."""
        category = self._extract_category(raw_layer_name)
        if category not in self.site_boundary_layer_priority:
            return None
        return self.site_boundary_layer_priority.index(category)
