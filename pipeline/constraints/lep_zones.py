"""Охранные зоны объектов электросетевого хозяйства (ЛЭП, кабельные линии).

Источник: Постановление Правительства РФ от 24.02.2009 № 160, п. 21-22
(см. docs/NORMATIVE_REFERENCES.md, data/reference/lep_protection_zones.yaml).

Это регулятивно ОТДЕЛЬНАЯ норма от арборикультурных отступов 743-ПП
(offset_registry.py) — зона ограничения хозяйственной деятельности вокруг
объекта электросетевого хозяйства, а не отступ конкретно для дерева/кустарника.
Ширина одинакова для дерева и кустарника (см. YAML).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_LEP_ZONES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "lep_protection_zones.yaml"
)


@dataclass(frozen=True)
class OverheadLineTier:
    voltage_kv_max: float | None
    voltage_kv_min: float | None
    width_m: float
    note: str | None


class LepZoneRegistry:
    def __init__(self, path: Path = DEFAULT_LEP_ZONES_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        require_keys(
            raw,
            {"source": dict, "overhead_lines": list, "underground_cable_lines": dict},
            source_path=path,
        )

        self.source_act_code: str = raw["source"]["act_code"]
        self.source_act_name: str = raw["source"]["act_name"]
        self.source_status: str = raw["source"]["status"]

        self._overhead_tiers: list[OverheadLineTier] = [
            OverheadLineTier(
                voltage_kv_max=t.get("voltage_kv_max"),
                voltage_kv_min=t.get("voltage_kv_min"),
                width_m=t["width_m"],
                note=t.get("note"),
            )
            for t in raw["overhead_lines"]
        ]
        self._underground_default_m: float = raw["underground_cable_lines"]["default_width_m"]

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def citation(self) -> str:
        return self.source_act_name

    def overhead_line_zone_width_m(self, voltage_kv: float) -> float:
        """Ширина охранной зоны ВЛ (в каждую сторону от крайних проводов), метры.

        Тарифные пороги заданы включительно по verstage_kv_max (таблица акта даёт
        точки, а не диапазоны формально — трактуем как "до X кВ включительно",
        что соответствует практике применения этой таблицы).
        """
        for tier in self._overhead_tiers:
            if tier.voltage_kv_max is not None and voltage_kv <= tier.voltage_kv_max:
                return tier.width_m
        # Класса напряжения выше самого высокого известного тарифа (1150 кВ) в таблице нет —
        # не домысливаем: вызывающий код должен явно обработать этот случай.
        raise ValueError(
            f"Класс напряжения {voltage_kv} кВ выше максимального известного тарифа "
            f"(1150 кВ) в Постановлении № 160 — ширина зоны не определена этой таблицей."
        )

    def underground_cable_zone_width_m(self) -> float:
        return self._underground_default_m

    def most_conservative_width_m(self) -> float:
        """Наибольшая известная ширина охранной зоны ВЛ (сейчас — 1150 кВ).

        Используется, когда класс напряжения ЛЭП не удаётся определить из
        исходных данных (см. pipeline/ingest/dxf_ingest.py — по имени слоя
        напряжение не различить) — консервативный отказ вместо угадывания:
        лучше временно исключить из посадки слишком большую площадь и явно
        пометить это как непроверенное допущение (verified=False, citation=None
        в ConstraintFeature), чем ошибочно выбрать маленькую зону и разрешить
        посадку там, где по факту может быть высоковольтная линия.
        """
        return max(tier.width_m for tier in self._overhead_tiers)
