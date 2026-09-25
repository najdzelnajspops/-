"""Отступ новой посадки от существующего сохраняемого насаждения.

НЕ норма — практическое допущение проекта, согласованное с пользователем
2026-09-15 (см. data/reference/existing_vegetation_buffer_policy.yaml для
полного обоснования и источников-ориентиров). Поэтому этот модуль всегда
возвращает verified=False вместе с числом — вызывающий код (buffer_engine
через unverified_fallback_m, либо напрямую) обязан прокидывать эту пометку
дальше в отчёт, а не выдавать её за подтверждённую норму 743-ПП.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline.common.schema_validation import require_keys, source_updated_at
from pipeline.constraints.buffer_engine import ConstraintFeature

EXISTING_TREE_PRESERVE_BOUNDARY_TYPE = "existing_tree_preserve"

DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reference"
    / "existing_vegetation_buffer_policy.yaml"
)


@dataclass(frozen=True)
class ExistingVegetationClearance:
    distance_m: float
    tier: str
    tier_label_ru: str
    verified: bool = False
    rationale: str = ""


class ExistingVegetationPolicy:
    def __init__(self, path: Path = DEFAULT_POLICY_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        require_keys(
            raw,
            {"vigor_tiers": dict, "unknown_vigor_default_tier": str, "rationale": str},
            source_path=path,
        )
        self._tiers: dict[str, dict] = raw["vigor_tiers"]
        self._unknown_default_tier: str = raw["unknown_vigor_default_tier"]
        self.rationale: str = raw["rationale"].strip()

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def clearance_for_tier(self, tier: str | None) -> ExistingVegetationClearance:
        """tier: одно из vigorous/medium/dwarf_columnar/shrub, либо None
        (вид/сила роста существующего насаждения неизвестны — берём самую
        консервативную категорию, чтобы не занизить отступ по недостатку данных)."""
        resolved_tier = tier or self._unknown_default_tier
        tier_data = self._tiers.get(resolved_tier)
        if tier_data is None:
            raise ValueError(f"Неизвестная категория силы роста: {resolved_tier!r}")
        return ExistingVegetationClearance(
            distance_m=tier_data["default_m"],
            tier=resolved_tier,
            tier_label_ru=tier_data["label_ru"],
            verified=False,
            rationale=self.rationale,
        )

    def apply_to_features(self, features: list[ConstraintFeature]) -> list[ConstraintFeature]:
        """Проставляет explicit_distance_m существующим сохраняемым насаждениям
        (boundary_type == existing_tree_preserve, см. layer_classifier.py — из
        дендроплана без указания вида/силы роста, поэтому всегда самая
        консервативная категория, tier=None).

        explicit_citation сознательно НЕ устанавливается (остаётся None) — по
        конвенции buffer_engine.py (`verified = explicit_citation is not None`)
        это единственный тип зоны в проекте, которая обязана попасть в отчёт
        интерпретации с verified=False, а не выглядеть подтверждённой нормой
        (см. docs/NORMATIVE_REFERENCES.md, раздел про существующие насаждения).
        Остальные объекты возвращаются без изменений.
        """
        result: list[ConstraintFeature] = []
        for feature in features:
            if feature.boundary_type != EXISTING_TREE_PRESERVE_BOUNDARY_TYPE:
                result.append(feature)
                continue
            clearance = self.clearance_for_tier(None)
            result.append(
                dataclasses.replace(
                    feature,
                    explicit_distance_m=clearance.distance_m,
                    explicit_citation=None,
                    label=(
                        feature.label
                        or f"Существующее сохраняемое насаждение (категория силы роста: "
                        f"{clearance.tier_label_ru}, допущение проекта — не норма)"
                    ),
                )
            )
        return result
