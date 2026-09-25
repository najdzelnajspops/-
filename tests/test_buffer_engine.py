"""Синтетический тест Constraint/Buffer Engine — без реального DXF (его ещё нет),
но с реальными нормативными отступами из offset_norms.yaml (743-ПП, Таблица 3.6.1).

Сценарий: квадратный участок 20x20 м, посередине проходит газопровод (линия) и
силовой кабель (линия) под прямым углом друг к другу. Проверяем, что:
1. Вблизи газопровода (<1.5 м) запрещено сажать дерево, но по кабелю с 0.7 м
   для кустарника — уже допустимо (у газопровода отступа для кустарника вообще нет
   в норме, значит проверка идёт только по колонке "дерево" в скипнутых объектах).
2. Каждая буферная зона несёт verified=True и ссылку на 743-ПП.
3. Инвазивный вид (Борщевик Сосновского) блокируется реестром инвазивных видов.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, Point, Polygon

from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.offset_registry import OffsetRegistry


def test_offset_registry_loads_verified_743pp_values():
    registry = OffsetRegistry()
    assert registry.source_act_code == "PP_743"
    assert registry.source_status == "verified"

    gas = registry.get_rule("gas_pipeline")
    assert gas is not None
    assert gas.min_distance_tree_m == 1.5
    assert gas.min_distance_shrub_m is None  # у газопровода нет нормы для кустарника

    cable = registry.get_rule("power_cable")
    assert cable.min_distance_tree_m == 2.0
    assert cable.min_distance_shrub_m == 0.7


def test_buffer_engine_forbids_tree_near_gas_pipeline():
    site = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    gas_pipeline = LineString([(10, 0), (10, 20)])  # вертикальная линия через центр

    registry = OffsetRegistry()
    features = [
        ConstraintFeature(id="gas-1", boundary_type="gas_pipeline", geometry=gas_pipeline),
    ]

    result = build_constraint_map(
        site_boundary=site,
        features=features,
        planting_kind="tree",
        registry=registry,
    )

    assert len(result.zone_sources) == 1
    zone = result.zone_sources[0]
    assert zone.verified is True
    assert zone.distance_m == 1.5
    assert "743-ПП" in zone.citation

    # Точка в 1 м от газопровода — внутри запрещённой зоны (норма 1.5 м).
    point_close = Point(9, 10)
    assert result.allowed_zone.contains(point_close) is False
    assert result.forbidden_zone.contains(point_close) is True

    # Точка в 3 м от газопровода — уже допустима.
    point_far = Point(7, 10)
    assert result.allowed_zone.contains(point_far) is True


def test_buffer_engine_skips_shrub_offset_where_norm_has_none():
    """У газопровода в 743-ПП/Таблице 3.6.1 нет нормы отступа для кустарника —
    движок обязан честно пропустить объект (skipped_features), а не выдумать число."""
    site = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    gas_pipeline = LineString([(10, 0), (10, 20)])
    registry = OffsetRegistry()

    result = build_constraint_map(
        site_boundary=site,
        features=[ConstraintFeature(id="gas-1", boundary_type="gas_pipeline", geometry=gas_pipeline)],
        planting_kind="shrub",
        registry=registry,
    )

    assert len(result.zone_sources) == 0
    assert len(result.skipped_features) == 1
    assert result.skipped_features[0].reason == "no_offset_defined_for_shrub"
    # Без буферов вся площадь участка формально допустима под кустарник по этой норме.
    assert result.allowed_zone.equals(site)


def test_unknown_boundary_type_is_never_silently_verified():
    """Неизвестный реестру тип границы (например, существующее сохраняемое дерево —
    для него нет отдельной нормы, это осознанное допущение, см. OPEN_QUESTIONS.md)
    либо пропускается, либо буферизуется с явной пометкой verified=False —
    никогда не выглядит как проверенная норма."""
    site = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    preserved_tree = Point(10, 10)
    registry = OffsetRegistry()

    # Без fallback — объект пропускается, не порождает ложной уверенности.
    result_no_fallback = build_constraint_map(
        site_boundary=site,
        features=[ConstraintFeature(id="existing-1", boundary_type="existing_tree_preserve", geometry=preserved_tree)],
        planting_kind="tree",
        registry=registry,
    )
    assert len(result_no_fallback.zone_sources) == 0
    assert "existing_tree_preserve" in result_no_fallback.unknown_boundary_types

    # С fallback — буферизуется, но verified=False и citation=None.
    result_with_fallback = build_constraint_map(
        site_boundary=site,
        features=[ConstraintFeature(id="existing-1", boundary_type="existing_tree_preserve", geometry=preserved_tree)],
        planting_kind="tree",
        registry=registry,
        unverified_fallback_m=5.0,
    )
    assert len(result_with_fallback.zone_sources) == 1
    zone = result_with_fallback.zone_sources[0]
    assert zone.verified is False
    assert zone.citation is None


def test_invasive_species_registry_blocks_known_invasive_species():
    registry = InvasiveSpeciesRegistry()

    hit = registry.check(name_ru="Борщевик Сосновского")
    assert hit is not None
    assert hit.group == "I"

    hit_lat = registry.check(name_lat="Physocarpus opulifolius")
    assert hit_lat is not None
    assert hit_lat.name_ru == "Пузыреплодник калинолистный"

    assert registry.is_invasive(name_ru="Липа мелколистная") is False


if __name__ == "__main__":
    test_offset_registry_loads_verified_743pp_values()
    test_buffer_engine_forbids_tree_near_gas_pipeline()
    test_buffer_engine_skips_shrub_offset_where_norm_has_none()
    test_unknown_boundary_type_is_never_silently_verified()
    test_invasive_species_registry_blocks_known_invasive_species()
    print("OK: все синтетические тесты Constraint Engine прошли")
