"""Тесты Агента ОТК, проверка 6.2 — целостность слоёв (pipeline/otk/layer_integrity.py).

Независимая проверка НЕ должна полагаться на внутренние допущения
pipeline/export/dxf_export.py — тесты явно портят DXF вручную через ezdxf,
не через сам Export Agent, чтобы убедиться, что проверка ловит нарушение,
даже если оно возникло не тем путём, которым Export Agent обычно работает.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.export.dxf_export import export_placement_to_dxf
from pipeline.otk.layer_integrity import check_layer_integrity
from pipeline.placement.generator import PlacementPoint, PlacementResult


def _make_base_dxf(path: Path) -> None:
    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    msp.add_point((5, 5), dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    doc.saveas(str(path))


def _placement(placed_xy: list[tuple[float, float]]) -> PlacementResult:
    points = [
        PlacementPoint(
            id=f"tree_{i:04d}",
            planting_kind="tree",
            x=x,
            y=y,
            status="placed",
            species_name_ru="Тестовый вид",
            species_citation="Тестовое обоснование",
            grid_spacing_m=6.0,
            grid_spacing_citation="тест",
        )
        for i, (x, y) in enumerate(placed_xy, start=1)
    ]
    return PlacementResult(planting_kind="tree", territory_category="dvorovye", points=points)


def test_legitimate_export_passes_integrity_check(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1), (2, 2)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert result.issues == []
    assert result.base_entity_count == 2  # LINE + POINT
    assert set(result.new_layers_found) == {"GREEN_AI_TREE_PROPOSED"}


def test_layer_integrity_passes_with_organizer_block_symbol(tmp_path):
    """С 2026-09-24 предложенные деревья рисуются реальным блоком организаторов
    (INSERT + внутренние CIRCLE/HATCH/LWPOLYLINE, см. pipeline/export/
    dxf_export.py, docs/DATA_STRUCTURE.md §8), а не примитивами. Явно
    проверяем, что при этом блок-специфичный риск — исходные слои блока
    ("СП_дендроплан" и т.п.) — не просачивается как новый не-GREEN_AI_* слой,
    и что внутри самого блока действительно есть перепривязанная геометрия
    (не пустой блок)."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1), (2, 2)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert set(result.new_layers_found) == {"GREEN_AI_TREE_PROPOSED"}

    doc = ezdxf.readfile(str(output_path))
    insert = next(e for e in doc.modelspace() if e.dxftype() == "INSERT")
    block_entities = list(doc.blocks.get(insert.dxf.name))
    assert block_entities
    assert all(e.dxf.layer == "GREEN_AI_TREE_PROPOSED" for e in block_entities)


def test_detects_missing_original_entity(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)
    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    # Портим результат вручную: удаляем исходную LINE-сущность.
    doc = ezdxf.readfile(str(output_path))
    msp = doc.modelspace()
    line = next(e for e in msp if e.dxftype() == "LINE")
    msp.delete_entity(line)
    doc.saveas(str(output_path))

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "violation"
    assert any(issue.kind == "entity_missing" for issue in result.issues)


def test_detects_modified_original_entity(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)
    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    # Портим результат вручную: сдвигаем координаты исходной LINE.
    doc = ezdxf.readfile(str(output_path))
    msp = doc.modelspace()
    line = next(e for e in msp if e.dxftype() == "LINE")
    line.dxf.end = (999, 999)
    doc.saveas(str(output_path))

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "violation"
    assert any(issue.kind == "entity_modified" for issue in result.issues)


def test_ignores_benign_ezdxf_roundtrip_default_drop(tmp_path):
    """Регрессионный тест на реальную находку 2026-09-17: ezdxf при readfile+saveas
    молча опускает `const_width=0.0` у LWPOLYLINE (явно установленное значение по
    умолчанию превращается в отсутствующий атрибут) — это нормализация формата,
    не смысловое изменение содержимого. Не должно считаться нарушением."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"

    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    poly = msp.add_lwpolyline([(0, 0), (5, 0), (5, 5)], dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    poly.dxf.const_width = 0.0
    doc.saveas(str(base_path))

    # export_placement_to_dxf уже делает readfile+saveas на базовом файле — сама
    # эта особенность ezdxf воспроизводится естественно, без ручной симуляции
    # (подтверждено при написании теста: ручной `del const_width` ниже упал бы
    # с DXFAttributeError "не существует" — атрибут уже отсутствует после export).
    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert result.issues == []


def test_still_detects_genuine_const_width_change(tmp_path):
    """const_width, изменённый на НЕ-умолчательное значение — настоящее
    изменение, должно ловиться (проверка не превратилась в дырявый allowlist)."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"

    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    poly = msp.add_lwpolyline([(0, 0), (5, 0), (5, 5)], dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    poly.dxf.const_width = 0.0
    doc.saveas(str(base_path))

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])
    doc2 = ezdxf.readfile(str(output_path))
    poly2 = next(e for e in doc2.modelspace() if e.dxftype() == "LWPOLYLINE")
    poly2.dxf.const_width = 3.5  # настоящее изменение, не умолчание
    doc2.saveas(str(output_path))

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "violation"
    assert any(issue.kind == "entity_modified" for issue in result.issues)


def test_ignores_benign_insert_scale_default_drop(tmp_path):
    """Регрессионный тест на реальную находку 2026-09-17: ezdxf при readfile+saveas
    молча опускает xscale/yscale/zscale=1.0 у INSERT (блоки-символы — деревья,
    светофоры, фонари, ЛЭП, крыльца в реальных объектах датасета) — нормализация
    формата, не смысловое изменение (масштаб 1.0 подразумевается умолчанию)."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"

    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    block = doc.blocks.new(name="TREE_SYMBOL")
    block.add_circle((0, 0), radius=1.0)
    msp = doc.modelspace()
    msp.add_blockref(
        "TREE_SYMBOL", (3, 3),
        dxfattribs={"layer": "EXISTING_COMMUNICATIONS", "xscale": 1.0, "yscale": 1.0, "zscale": 1.0},
    )
    doc.saveas(str(base_path))

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert result.issues == []


def test_ignores_benign_ellipse_extrusion_default_drop(tmp_path):
    """Регрессионный тест на реальную находку 2026-09-17 («Олимпийская деревня»,
    2525 сущностей): ezdxf при readfile+saveas молча опускает явно установленный
    extrusion=(0.0, 0.0, 1.0) у ELLIPSE («без поворота плоскости» — умолчание
    формата DXF) — нормализация, не смысловое изменение."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"

    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    ellipse = msp.add_ellipse(
        (2, 2), major_axis=(1, 0), ratio=0.5, dxfattribs={"layer": "EXISTING_COMMUNICATIONS"}
    )
    ellipse.dxf.extrusion = (0.0, 0.0, 1.0)
    doc.saveas(str(base_path))

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert result.issues == []


def test_ignores_benign_mtext_flow_direction_default_drop(tmp_path):
    """Регрессионный тест на реальную находку 2026-09-17 («Олимпийская деревня»,
    5 сущностей): ezdxf при readfile+saveas молча опускает явно установленный
    flow_direction=1 у MTEXT («слева направо» — умолчание по спецификации DXF
    group code 76) — нормализация формата, не смысловое изменение."""
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"

    doc = ezdxf.new(setup=False)
    doc.layers.add(name="EXISTING_COMMUNICATIONS")
    msp = doc.modelspace()
    mtext = msp.add_mtext("Тестовая подпись", dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    mtext.dxf.insert = (4, 4, 0)
    mtext.dxf.flow_direction = 1
    doc.saveas(str(base_path))

    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "ok"
    assert result.issues == []


def test_detects_new_entity_in_non_green_ai_layer(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)
    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    # Портим результат вручную: добавляем сущность прямо в исходный слой,
    # а не в GREEN_AI_* — именно тот сценарий, для которого ОТК и нужен.
    doc = ezdxf.readfile(str(output_path))
    msp = doc.modelspace()
    msp.add_point((7, 7), dxfattribs={"layer": "EXISTING_COMMUNICATIONS"})
    doc.saveas(str(output_path))

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "violation"
    assert any(issue.kind == "unexpected_new_entity" for issue in result.issues)


def test_detects_unexpected_new_layer_name(tmp_path):
    base_path = tmp_path / "base.dxf"
    output_path = tmp_path / "output.dxf"
    _make_base_dxf(base_path)
    export_placement_to_dxf(base_path, output_path, [_placement([(1, 1)])])

    doc = ezdxf.readfile(str(output_path))
    doc.layers.add(name="NOT_GREEN_AI_SOMETHING")
    doc.modelspace().add_point((8, 8), dxfattribs={"layer": "NOT_GREEN_AI_SOMETHING"})
    doc.saveas(str(output_path))

    result = check_layer_integrity(base_path, output_path)
    assert result.verdict == "violation"
    assert any(issue.kind == "unexpected_new_layer" for issue in result.issues)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_legitimate_export_passes_integrity_check(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_detects_missing_original_entity(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_detects_modified_original_entity(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_detects_new_entity_in_non_green_ai_layer(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_detects_unexpected_new_layer_name(Path(d))
    print("OK: layer integrity tests passed")
