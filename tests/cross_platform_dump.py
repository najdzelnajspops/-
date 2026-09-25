"""Не тест pytest, а служебный скрипт для прямой кросс-платформенной сверки:
запускает фиксированный сценарий Constraint Engine и пишет результат в
детерминированном текстовом виде (WKT с фиксированной точностью + JSON отчёта),
чтобы вывод на Windows и на Linux (WSL/Docker) можно было сравнить построчно,
а не просто "оба теста прошли на своей платформе".

Запуск: python tests/cross_platform_dump.py > out_<platform>.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, Point, Polygon

from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.constraints.offset_registry import OffsetRegistry

WKT_PRECISION = 6  # фиксированная точность вывода координат — иначе разные платформы
                    # могут напечатать один и тот же float чуть по-разному в repr()


def main() -> None:
    site = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])

    lep = LepZoneRegistry()
    lep_width = lep.overhead_line_zone_width_m(voltage_kv=110)

    features = [
        ConstraintFeature(id="gas-1", boundary_type="gas_pipeline", geometry=LineString([(10, 0), (10, 50)])),
        ConstraintFeature(id="water-1", boundary_type="water_supply", geometry=LineString([(0, 25), (50, 25)])),
        ConstraintFeature(id="cable-1", boundary_type="power_cable", geometry=Point(35, 35)),
        ConstraintFeature(
            id="lep-1",
            boundary_type="power_line_overhead_110kv",
            geometry=LineString([(40, 0), (40, 50)]),
            label="ЛЭП 110 кВ",
            explicit_distance_m=lep_width,
            explicit_citation=lep.citation(),
        ),
    ]

    registry = OffsetRegistry()
    result = build_constraint_map(site, features, "tree", registry)

    print("=== ALLOWED ZONE WKT ===")
    print(result.allowed_zone.wkt)
    print()
    print("=== FORBIDDEN ZONE WKT ===")
    print(result.forbidden_zone.wkt)
    print()
    print("=== ALLOWED ZONE AREA (округлено до 6 знаков) ===")
    print(round(result.allowed_zone.area, WKT_PRECISION))
    print()
    print("=== REPORT ROWS ===")
    for row in result.to_report_rows():
        print(row)
    print()
    print("=== UNKNOWN BOUNDARY TYPES ===")
    print(sorted(result.unknown_boundary_types))


if __name__ == "__main__":
    main()
