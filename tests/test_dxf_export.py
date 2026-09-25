"""Тесты DXF Export Agent (pipeline/export/dxf_export.py).

Ключевая проверка ТЗ п.3.1: исходные слои/сущности не меняются и не
перезаписываются — результат только в новых, отдельных слоях.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.export.dxf_export import BaseFileWouldBeOverwrittenError, export_placement_to_dxf
from pipeline.placement.generator import PlacementPoint, PlacementResult


def _make_base_dxf(path: Path) -> None:
    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    doc.saveas(str(path))


def _placement(planting_kind: str, placed_xy: list[tuple[float, float]], rejected_xy: list[tuple[float, float]]):
    points = []
    for i, (x, y) in enumerate(placed_xy, start=1):
        points.append(
            PlacementPoint(
                id=f"{planting_kind}_{i:04d}",
                planting_kind=planting_kind,
                x=x,
                y=y,
                status="placed",
                species_name_ru="Тестовый вид",
                species_citation="Тестовое обоснование",
                grid_spacing_m=6.0,
                grid_spacing_citation="тест",
            )
        )
    for i, (x, y) in enumerate(rejected_xy, start=len(placed_xy) + 1):
        points.append(
            PlacementPoint(
                id=f"{planting_kind}_{i:04d}",
                planting_kind=planting_kind,
                x=x,
                y=y,
                status="no_recommended_species",
                species_name_ru=None,
                species_citation=None,
                grid_spacing_m=6.0,
                grid_spacing_citation="тест",
            )
        )
    return PlacementResult(planting_kind=planting_kind, territory_category="dvorovye", points=points)


def test_export_adds_new_layers_without_touching_original(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    placement = _placement("tree", placed_xy=[(1, 1), (2, 2)], rejected_xy=[(3, 3)])
    summary = export_placement_to_dxf(base_path, output_path, [placement])

    assert summary.total_entities() == 3  # 2 placed + 1 rejected
    layer_names = {layer.layer_name for layer in summary.layers}
    assert layer_names == {"GREEN_AI_TREE_PROPOSED", "GREEN_AI_TREE_REJECTED"}

    proposed = next(layer for layer in summary.layers if layer.layer_name == "GREEN_AI_TREE_PROPOSED")
    rejected = next(layer for layer in summary.layers if layer.layer_name == "GREEN_AI_TREE_REJECTED")
    assert proposed.entity_count == 2
    assert rejected.entity_count == 1

    # Исходный файл НЕ тронут: перечитываем его напрямую (не output_path).
    base_doc_after = ezdxf.readfile(str(base_path))
    base_layer_names = {layer.dxf.name for layer in base_doc_after.layers}
    assert "GREEN_AI_TREE_PROPOSED" not in base_layer_names
    base_msp = base_doc_after.modelspace()
    base_entities = list(base_msp)
    assert len(base_entities) == 1
    assert base_entities[0].dxftype() == "LINE"
    assert base_entities[0].dxf.layer == "EXISTING_COMMUNICATIONS"

    # В выходном файле — и старая геометрия (нетронутая), и новые слои.
    output_doc = ezdxf.readfile(str(output_path))
    output_msp = output_doc.modelspace()
    original_layer_entities = [e for e in output_msp if e.dxf.layer == "EXISTING_COMMUNICATIONS"]
    assert len(original_layer_entities) == 1
    assert original_layer_entities[0].dxftype() == "LINE"
    new_layer_entities = [e for e in output_msp if e.dxf.layer == "GREEN_AI_TREE_PROPOSED"]
    # 2 placed-точки * (INSERT реального блока организаторов + TEXT id) = 4
    # сущности верхнего уровня в modelspace (условный знак блока — CIRCLE/HATCH/
    # LWPOLYLINE — лежит ВНУТРИ определения блока, не в modelspace напрямую, см.
    # test_placed_tree_uses_organizer_block_symbol).
    assert len(new_layer_entities) == 4
    assert sum(1 for e in new_layer_entities if e.dxftype() == "INSERT") == 2
    assert sum(1 for e in new_layer_entities if e.dxftype() == "TEXT") == 2


def test_export_refuses_to_overwrite_base_file(tmp_path):
    base_path = tmp_path / "base.dxf"
    _make_base_dxf(base_path)
    placement = _placement("shrub", placed_xy=[(1, 1)], rejected_xy=[])

    with pytest.raises(BaseFileWouldBeOverwrittenError):
        export_placement_to_dxf(base_path, base_path, [placement])


def test_export_with_only_rejected_points_creates_only_rejected_layer(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    placement = _placement("shrub", placed_xy=[], rejected_xy=[(5, 5)])
    summary = export_placement_to_dxf(base_path, output_path, [placement])

    assert {layer.layer_name for layer in summary.layers} == {"GREEN_AI_SHRUB_REJECTED"}


def _shrub_placement_with_group_spacing(clusters_xy: list[list[tuple[float, float]]], spacing_m: float):
    """Строит PlacementResult кустарника из готовых групп координат — имитирует
    реальный вывод ClusterConfig-кластеризации генератора (см. pipeline/placement/
    generator.py) без запуска самого генератора."""
    points = []
    idx = 0
    for cluster in clusters_xy:
        for x, y in cluster:
            idx += 1
            points.append(
                PlacementPoint(
                    id=f"shrub_{idx:04d}",
                    planting_kind="shrub",
                    x=x,
                    y=y,
                    status="placed",
                    species_name_ru="Тестовый кустарник",
                    species_citation="Тестовое обоснование",
                    grid_spacing_m=spacing_m,
                    grid_spacing_citation="тест",
                )
            )
    return PlacementResult(planting_kind="shrub", territory_category="dvorovye", points=points)


def test_rejected_tree_symbol_is_gost_circle_with_cross_by_geometry(tmp_path):
    """ГОСТ 21.508-2020 — дерево-заглушка (свой круг-с-крестом) остаётся для
    ОТКЛОНЁННЫХ точек: для них вид не выбирается (см. generator.py), значит
    хвойное/лиственное неизвестно и блок организаторов подобрать нельзя (см.
    докстринг pipeline/export/dxf_export.py, раздел "РЕАЛЬНЫЕ БЛОКИ
    ОРГАНИЗАТОРОВ"). Проверяем не только типы сущностей, но и реальную
    геометрию: центр окружности и пересечение линий совпадают с точкой."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    placement = _placement("tree", placed_xy=[], rejected_xy=[(10.0, 20.0)])
    export_placement_to_dxf(base_path, output_path, [placement])

    doc = ezdxf.readfile(str(output_path))
    entities = [e for e in doc.modelspace() if e.dxf.layer == "GREEN_AI_TREE_REJECTED"]
    circle = next(e for e in entities if e.dxftype() == "CIRCLE")
    assert circle.dxf.center.x == pytest.approx(10.0)
    assert circle.dxf.center.y == pytest.approx(20.0)
    lines = [e for e in entities if e.dxftype() == "LINE"]
    assert len(lines) == 2
    # Крест: одна линия горизонтальная через (10,20), другая вертикальная через (10,20)
    midpoints = [((l.dxf.start.x + l.dxf.end.x) / 2, (l.dxf.start.y + l.dxf.end.y) / 2) for l in lines]
    for mx, my in midpoints:
        assert mx == pytest.approx(10.0)
        assert my == pytest.approx(20.0)


def test_placed_tree_uses_organizer_block_symbol(tmp_path):
    """Задача из docs/DATA_STRUCTURE.md §8: для ПРЕДЛОЖЕННЫХ (placed) деревьев
    используется реальный блок организаторов (см. data/reference/
    planting_symbols.dxf), а не собственный примитив, и все его внутренние
    сущности перепривязаны на GREEN_AI_TREE_PROPOSED (иначе агент ОТК 6.2
    нашёл бы новые слои — см. test_layer_integrity_passes_with_organizer_block_symbol)."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    placement = _placement("tree", placed_xy=[(10.0, 20.0)], rejected_xy=[])
    export_placement_to_dxf(base_path, output_path, [placement])

    doc = ezdxf.readfile(str(output_path))
    entities = [e for e in doc.modelspace() if e.dxf.layer == "GREEN_AI_TREE_PROPOSED"]
    insert = next(e for e in entities if e.dxftype() == "INSERT")
    assert insert.dxf.insert.x == pytest.approx(10.0)
    assert insert.dxf.insert.y == pytest.approx(20.0)

    block = doc.blocks.get(insert.dxf.name)
    block_entities = list(block)
    assert block_entities  # блок реально непустой (не заглушка)
    assert all(e.dxf.layer == "GREEN_AI_TREE_PROPOSED" for e in block_entities)
    # ни один слой-источник блока не должен просочиться в итоговый документ
    output_layer_names = {layer.dxf.name for layer in doc.layers}
    assert "СП_дендроплан" not in output_layer_names
    assert "СП_зеленые насаждения для ГП" not in output_layer_names


def test_placed_conifer_and_deciduous_trees_use_different_blocks(tmp_path):
    """Хвойное/лиственное определяется по `chosen_species_entry.life_form_group_ru`
    (см. `_pick_tree_symbol_source_block`) — разные виды должны давать разные
    определения блока в итоговом документе."""
    from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesEntry

    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    deciduous_point = PlacementPoint(
        id="tree_0001", planting_kind="tree", x=1.0, y=1.0, status="placed",
        species_name_ru="Липа мелколистная", species_citation="тест",
        grid_spacing_m=6.0, grid_spacing_citation="тест",
    )
    deciduous_result = PlacementResult(
        planting_kind="tree", territory_category="dvorovye", points=[deciduous_point],
        chosen_species_entry=DpioosSpeciesEntry(
            name_ru="Липа мелколистная", life_form="tree", life_form_group_ru="Лиственные деревья",
            source="тест", footnotes=(), territory_flags={},
        ),
    )
    conifer_point = PlacementPoint(
        id="tree_0002", planting_kind="tree", x=2.0, y=2.0, status="placed",
        species_name_ru="Ель", species_citation="тест",
        grid_spacing_m=6.0, grid_spacing_citation="тест",
    )
    conifer_result = PlacementResult(
        planting_kind="tree", territory_category="dvorovye", points=[conifer_point],
        chosen_species_entry=DpioosSpeciesEntry(
            name_ru="Ель", life_form="tree", life_form_group_ru="Хвойные деревья",
            source="тест", footnotes=(), territory_flags={},
        ),
    )
    export_placement_to_dxf(base_path, output_path, [deciduous_result, conifer_result])

    doc = ezdxf.readfile(str(output_path))
    inserts = {e.dxf.insert.x: e.dxf.name for e in doc.modelspace() if e.dxftype() == "INSERT"}
    assert inserts[1.0] != inserts[2.0]


def test_shrub_group_drawn_as_single_mass_with_count_label(tmp_path):
    """ГОСТ 21.508-2020 — кустарник группой рисуется одним «пятном» с ОДНОЙ
    подписью числа экземпляров, не точкой на каждый куст."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    # Плотная группа 5 точек (шаг 0.5 м) — типичная группа кустарника.
    cluster = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (0.0, 0.5), (0.5, 0.5)]
    placement = _shrub_placement_with_group_spacing([cluster], spacing_m=0.5)
    summary = export_placement_to_dxf(base_path, output_path, [placement])

    proposed = next(layer for layer in summary.layers if layer.layer_name == "GREEN_AI_SHRUB_PROPOSED")
    assert proposed.entity_count == 5  # число экземпляров сохранено для отчётности, хоть и одна фигура на чертеже

    doc = ezdxf.readfile(str(output_path))
    entities = [e for e in doc.modelspace() if e.dxf.layer == "GREEN_AI_SHRUB_PROPOSED"]
    texts = [e for e in entities if e.dxftype() == "TEXT"]
    assert len(texts) == 1  # одна подпись на всю группу, не 5
    assert "5 шт." in texts[0].dxf.text
    assert sum(1 for e in entities if e.dxftype() == "LWPOLYLINE") == 1  # один контур "пятна"


def test_shrub_two_distant_groups_stay_separate(tmp_path):
    """Две группы, разнесённые далеко друг от друга (типичное расстояние МЕЖДУ
    группами по 623-ПП/густоте), не должны слиться в одно пятно."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    group_a = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5)]
    group_b = [(50.0, 50.0), (50.5, 50.0), (50.0, 50.5)]  # далеко от группы A
    placement = _shrub_placement_with_group_spacing([group_a, group_b], spacing_m=0.5)
    summary = export_placement_to_dxf(base_path, output_path, [placement])

    doc = ezdxf.readfile(str(output_path))
    entities = [e for e in doc.modelspace() if e.dxf.layer == "GREEN_AI_SHRUB_PROPOSED"]
    texts = [e for e in entities if e.dxftype() == "TEXT"]
    assert len(texts) == 2  # две отдельные подписи — группы не слились
    counts = sorted(int(t.dxf.text.split("(")[1].split(" ")[0]) for t in texts)
    assert counts == [3, 3]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_export_adds_new_layers_without_touching_original(Path(d))
    print("OK: run via pytest for full coverage (includes pytest.raises test)")
