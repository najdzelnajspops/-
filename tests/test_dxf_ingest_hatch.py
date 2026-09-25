"""Тесты поддержки сущности HATCH в DXF Ingest Agent (pipeline/ingest/dxf_ingest.py).

Найдено на реальном объекте («Камчатская улица», слой «Красные линии»,
2026-09-17, см. docs/DATA_STRUCTURE.md): область отрисована не полигоном, а
~4000 мелких HATCH-плиток. Тесты — на синтетическом DXF (не зависят от датасета).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from pipeline.ingest.dxf_ingest import ingest_dxf


def _build_dxf_with_hatches(path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.new(name="obj|Граница площадки")
    doc.layers.new(name="obj|Красные линии")

    # Граница участка — квадрат 0..100
    msp.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "obj|Граница площадки"}
    )

    # HATCH через PolylinePath (готовые вершины) — квадрат 10x10 в углу (0,0)-(10,10)
    h1 = msp.add_hatch(dxfattribs={"layer": "obj|Красные линии"})
    h1.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)

    # HATCH через EdgePath (LineEdge, как на реальных данных) — квадрат 5x5 в углу (20,0)-(25,5)
    h2 = msp.add_hatch(dxfattribs={"layer": "obj|Красные линии"})
    ep = h2.paths.add_edge_path()
    ep.add_line((20, 0), (25, 0))
    ep.add_line((25, 0), (25, 5))
    ep.add_line((25, 5), (20, 5))
    ep.add_line((20, 5), (20, 0))

    doc.saveas(str(path))


def test_hatch_edge_path_becomes_polygon_feature_with_correct_area():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "test.dxf"
        _build_dxf_with_hatches(dxf_path)
        result = ingest_dxf(str(dxf_path))

        red_line_features = [f for f in result.features if f.boundary_type == "red_lines_no_planting"]
        assert len(red_line_features) == 2

        areas = sorted(f.geometry.area for f in red_line_features)
        assert areas == [25.0, 100.0]  # 5x5 (EdgePath) и 10x10 (PolylinePath)


def test_hatch_not_in_skipped_entity_types():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "test.dxf"
        _build_dxf_with_hatches(dxf_path)
        result = ingest_dxf(str(dxf_path))

        assert "HATCH" not in result.skipped_entity_types


def test_hatch_with_hole_produces_polygon_with_reduced_area():
    """Многопутевой HATCH (внешний контур + вырез) — не встречен на реальных
    данных этого проекта (все проверенные HATCH слоя «Красные линии» —
    однопутевые), но обрабатывается по эвристике «наибольший путь — внешний
    контур, остальные — вырезы», не должен падать."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "test.dxf"
        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        doc.layers.new(name="obj|Красные линии")

        h = msp.add_hatch(dxfattribs={"layer": "obj|Красные линии"})
        h.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True)  # внешний, 400 м²
        h.paths.add_polyline_path([(5, 5), (15, 5), (15, 15), (5, 15)], is_closed=True)  # вырез, 100 м²
        doc.saveas(str(dxf_path))

        result = ingest_dxf(str(dxf_path))
        red_line_features = [f for f in result.features if f.boundary_type == "red_lines_no_planting"]
        assert len(red_line_features) == 1
        assert red_line_features[0].geometry.area == 300.0  # 400 - 100 (вырез)


if __name__ == "__main__":
    test_hatch_edge_path_becomes_polygon_feature_with_correct_area()
    test_hatch_not_in_skipped_entity_types()
    test_hatch_with_hole_produces_polygon_with_reduced_area()
    print("OK: HATCH ingest tests passed")
