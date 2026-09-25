"""Тесты, фиксирующие ОСОЗНАННЫЕ текущие ограничения системы — не баги, а
задокументированные границы объёма (см. docs/ARCHITECTURE.md §3.1b,
docs/OPEN_QUESTIONS.md). Цель: если кто-то в будущем случайно (рефакторингом)
изменит это поведение, тест должен упасть и заставить осознанно решить,
действительно ли ограничение снимается, а не разъехаться незаметно.

Найдено по прямому вопросу пользователя (2026-09-15): у нас нет данных по
глубине/типу корневой системы растений, и отступ не различается по видам
внутри категории «дерево»/«кустарник» — куст с компактным комом и агрессивный
куст (ива, тополь) получают один и тот же отступ."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, Polygon

from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.offset_registry import OffsetRegistry


def test_offset_is_identical_regardless_of_species_label():
    """ТЕКУЩЕЕ (осознанное) поведение: ConstraintFeature.label — произвольная
    подпись для человека, она НИКАК не влияет на расчёт отступа. Отступ зависит
    только от boundary_type (тип сети) и planting_kind (tree/shrub) — не от
    того, какой конкретно вид растения имеется в виду."""
    site = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    gas_pipeline = LineString([(10, 0), (10, 20)])
    registry = OffsetRegistry()

    compact_shrub = ConstraintFeature(
        id="shrub-compact",
        boundary_type="gas_pipeline",
        geometry=gas_pipeline,
        label="Спирея японская (компактный ком, неагрессивная корневая система)",
    )
    aggressive_shrub = ConstraintFeature(
        id="shrub-aggressive",
        boundary_type="gas_pipeline",
        geometry=gas_pipeline,
        label="Ива ломкая (агрессивная поверхностная корневая система, тянется к трубам)",
    )

    result_compact = build_constraint_map(site, [compact_shrub], "shrub", registry)
    result_aggressive = build_constraint_map(site, [aggressive_shrub], "shrub", registry)

    # Оба случая: у газопровода в 743-ПП/Таблице 3.6.1 нет нормы для кустарника
    # вообще (см. offset_norms.yaml — min_distance_shrub_m: null для gas_pipeline),
    # поэтому оба ОДИНАКОВО пропускаются без буфера — не потому что система
    # "разобралась", что ива опаснее, а потому что вида в записи нет вообще.
    assert len(result_compact.zone_sources) == 0
    assert len(result_aggressive.zone_sources) == 0
    assert result_compact.skipped_features[0].reason == result_aggressive.skipped_features[0].reason

    # На сети, где норма для кустарника ЕСТЬ (силовой кабель), убеждаемся, что
    # расстояние буквально идентично для "компактного" и "агрессивного" —
    # это и есть задокументированное ограничение, а не желаемое поведение.
    power_cable = LineString([(10, 0), (10, 20)])
    compact_on_cable = ConstraintFeature(id="c1", boundary_type="power_cable", geometry=power_cable, label="компактный")
    aggressive_on_cable = ConstraintFeature(id="c2", boundary_type="power_cable", geometry=power_cable, label="агрессивный (ива)")

    r1 = build_constraint_map(site, [compact_on_cable], "shrub", registry)
    r2 = build_constraint_map(site, [aggressive_on_cable], "shrub", registry)
    assert r1.zone_sources[0].distance_m == r2.zone_sources[0].distance_m  # ОГРАНИЧЕНИЕ: должно бы отличаться в реальности


def test_offset_registry_has_no_species_aware_lookup_mechanism():
    """Явная фиксация отсутствия механизма: OffsetRegistry в принципе не
    принимает вид растения как параметр — только boundary_type и planting_kind
    (tree/shrub). Если этот тест сломается после рефакторинга (появится новый
    параметр вроде species/root_aggressiveness), это ЖЕЛАТЕЛЬНОЕ развитие — тест
    нужно будет осознанно обновить, а не просто удалить."""
    registry = OffsetRegistry()
    import inspect

    sig = inspect.signature(registry.min_distance)
    assert set(sig.parameters.keys()) == {"boundary_type", "planting_kind"}


if __name__ == "__main__":
    test_offset_is_identical_regardless_of_species_label()
    test_offset_registry_has_no_species_aware_lookup_mechanism()
    print("OK: known-limitation pin tests passed (see docstrings — this documents a gap, not a success)")
