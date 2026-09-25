"""Повторяемость результата — по прямому требованию заказчика/жюри: один и тот же
вход должен на любом прогоне давать один и тот же результат (см. docs/ARCHITECTURE.md,
раздел «Повторяемость результата (обязательное требование к демо)»).

Это не абстрактная забота о качестве кода — жюри прямо сказали, что могут попросить
повторить прогон на демо и сверить, что результат не изменился. Поэтому здесь —
не юнит-тест одной функции, а тест ИНВАРИАНТА: два независимых вызова с одним и тем
же входом обязаны дать геометрически и по составу объяснений идентичный результат.

ЧЕСТНО О ГРАНИЦАХ ЭТОГО ТЕСТА (см. также docs/ARCHITECTURE.md §3.1):
эти тесты доказывают отсутствие СЛУЧАЙНОЙ/НЕЯВНОЙ недетерминированности внутри
Constraint Engine на ЭТОЙ машине с ЭТИМИ версиями shapely/GEOS (см. requirements.txt —
версии зафиксированы точно, не диапазоном, именно по этой причине). Они НЕ доказывают:
  - что результат будет идентичен между dev-машиной (Windows) и Docker-образом
    (Linux/MosTex.OS) — это отдельная, ещё не выполненная проверка, см. CHECKLIST.md;
  - что весь будущий пайплайн детерминирован — Placement Generator (алгоритм выбора
    конкретных точек посадки внутри allowed_zone) ещё не написан, а это самое рискованное
    по недетерминированности место (порядок обхода геометрии, возможные tie-break при
    равном расстоянии) — тестировать нужно будет отдельно, когда он появится.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, Point, Polygon

from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.offset_registry import OffsetRegistry


def _run_once():
    site = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)])
    features = [
        ConstraintFeature(id="gas-1", boundary_type="gas_pipeline", geometry=LineString([(10, 0), (10, 30)])),
        ConstraintFeature(id="water-1", boundary_type="water_supply", geometry=LineString([(0, 15), (30, 15)])),
        ConstraintFeature(id="cable-1", boundary_type="power_cable", geometry=Point(20, 20)),
    ]
    registry = OffsetRegistry()
    return build_constraint_map(
        site_boundary=site,
        features=features,
        planting_kind="tree",
        registry=registry,
    )


def test_two_independent_runs_produce_identical_result():
    result_a = _run_once()
    result_b = _run_once()

    # Геометрия допустимой/запрещённой зоны должна совпадать с точностью до shapely.equals
    # (а не просто "похожа") — иначе на демо возможен визуально незаметный, но реальный разъезд.
    assert result_a.allowed_zone.equals(result_b.allowed_zone)
    assert result_a.forbidden_zone.equals(result_b.forbidden_zone)

    # Состав и ПОРЯДОК обоснований должны совпадать — порядок важен, потому что он
    # напрямую попадает в отчёт интерпретации (docs/REPORT_DESIGN.md); если порядок
    # "плавает" между прогонами (например, из-за неупорядоченного set/dict где-то
    # в Ingest), это будет выглядеть как нестабильный результат на демо жюри.
    rows_a = result_a.to_report_rows()
    rows_b = result_b.to_report_rows()
    assert rows_a == rows_b

    # На всякий случай — 10 повторов подряд, не только 2.
    reference = _run_once().to_report_rows()
    for _ in range(10):
        assert _run_once().to_report_rows() == reference


def test_reproducibility_holds_on_adversarial_overlapping_geometry():
    """Простой прямоугольник с двумя непересекающимися линиями (как в тесте выше)
    почти не нагружает unary_union — GEOS обычно не путается на таких входах
    даже без реальной проверки. Здесь — специально плотная сетка из МНОГИХ
    пересекающихся и касающихся друг друга буферов (именно на таких входах
    cascaded union внутри GEOS исторически имел хрупкие места: порядок обхода
    дерева STRtree, точки касания на грани численной точности). Если
    повторяемость держится и здесь — это куда более сильное доказательство,
    чем не пересекающиеся линии на пустом прямоугольнике."""
    site = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])

    features = []
    # Частая сетка линий коммуникаций, буферы которых заведомо пересекаются
    # и местами касаются друг друга впритык (шаг 2 м при отступе 2 м у кабеля).
    for i in range(20):
        features.append(
            ConstraintFeature(
                id=f"cable-h-{i}",
                boundary_type="power_cable",
                geometry=LineString([(0, i * 2.5), (50, i * 2.5)]),
            )
        )
    for i in range(20):
        features.append(
            ConstraintFeature(
                id=f"cable-v-{i}",
                boundary_type="power_cable",
                geometry=LineString([(i * 2.5, 0), (i * 2.5, 50)]),
            )
        )

    registry = OffsetRegistry()

    def run():
        return build_constraint_map(site, features, "tree", registry)

    reference = run()
    reference_rows = reference.to_report_rows()

    for _ in range(25):
        result = run()
        assert result.allowed_zone.equals(reference.allowed_zone)
        assert result.forbidden_zone.equals(reference.forbidden_zone)
        assert result.to_report_rows() == reference_rows


def test_feature_order_in_input_does_not_change_final_allowed_zone():
    """Порядок объектов на входе (например, порядок сущностей в DXF) не должен
    влиять на итоговую геометрию допустимой зоны — объединение буферов коммутативно,
    но explicit-проверка нужна, чтобы будущий рефакторинг это не сломал незаметно."""
    site = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)])
    f1 = ConstraintFeature(id="gas-1", boundary_type="gas_pipeline", geometry=LineString([(10, 0), (10, 30)]))
    f2 = ConstraintFeature(id="water-1", boundary_type="water_supply", geometry=LineString([(0, 15), (30, 15)]))
    registry = OffsetRegistry()

    result_forward = build_constraint_map(site, [f1, f2], "tree", registry)
    result_reversed = build_constraint_map(site, [f2, f1], "tree", registry)

    assert result_forward.allowed_zone.equals(result_reversed.allowed_zone)


if __name__ == "__main__":
    test_two_independent_runs_produce_identical_result()
    test_reproducibility_holds_on_adversarial_overlapping_geometry()
    test_feature_order_in_input_does_not_change_final_allowed_zone()
    print("OK: reproducibility invariant holds (Constraint Engine only, single-platform)")
