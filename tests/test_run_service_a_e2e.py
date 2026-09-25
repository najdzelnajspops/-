"""Сквозной тест Service A: синтетический DXF -> Ingest -> Constraint Engine ->
Placement Generator -> DXF Export -> JSON-отчёт (pipeline/run.py).

Не заменяет проверку на реальном объекте датасета (см. tests/test_dxf_ingest.py) —
это быстрый детерминированный тест на маленькой синтетической геометрии,
чтобы проверить, что все агенты действительно связаны в один рабочий путь.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.run import run_service_a


def _make_synthetic_object_dxf(path: Path) -> None:
    doc = ezdxf.new(setup=False)
    for layer in ("Граница площадки", "Газопровод", "Отдельно стоящее дерево"):
        doc.layers.add(name=layer)
    msp = doc.modelspace()

    boundary = msp.add_lwpolyline(
        [(0, 0), (30, 0), (30, 30), (0, 30)], dxfattribs={"layer": "Граница площадки"}
    )
    boundary.closed = True

    msp.add_line((1, 0), (1, 30), dxfattribs={"layer": "Газопровод"})
    msp.add_point((15, 15), dxfattribs={"layer": "Отдельно стоящее дерево"})

    doc.saveas(str(path))


def test_service_a_end_to_end_on_synthetic_object(tmp_path):
    input_dxf = tmp_path / "object.dxf"
    output_dxf = tmp_path / "object_result.dxf"
    output_report = tmp_path / "report.json"
    _make_synthetic_object_dxf(input_dxf)

    result = run_service_a(
        input_dxf_paths=[input_dxf],
        base_dxf_path_for_export=input_dxf,
        territory_category="dvorovye",
        output_dxf_path=output_dxf,
        output_report_path=output_report,
    )

    assert result.ingest_result.site_boundary_status == "found"
    # Газопровод (1.5 м) и существующее дерево (6 м, консервативная категория)
    # должны попасть в применённые нормы дерева.
    tree_boundary_types = {zs.boundary_type for zs in result.constraint_results["tree"].zone_sources}
    assert "gas_pipeline" in tree_boundary_types
    assert "existing_tree_preserve" in tree_boundary_types

    veg_zone = next(
        zs for zs in result.constraint_results["tree"].zone_sources if zs.boundary_type == "existing_tree_preserve"
    )
    assert veg_zone.verified is False  # практическое допущение, не норма
    assert veg_zone.distance_m == 6

    gas_zone = next(
        zs for zs in result.constraint_results["tree"].zone_sources if zs.boundary_type == "gas_pipeline"
    )
    assert gas_zone.verified is True  # 743-ПП, подтверждённая норма

    # Участок 30x30 достаточно большой — на нём должны появиться и деревья, и кусты.
    assert len(result.placement_results["tree"].placed_points()) > 0
    assert len(result.placement_results["shrub"].placed_points()) > 0

    # DXF-результат существует и содержит новые слои.
    assert output_dxf.exists()
    output_doc = ezdxf.readfile(str(output_dxf))
    output_layer_names = {layer.dxf.name for layer in output_doc.layers}
    assert "GREEN_AI_TREE_PROPOSED" in output_layer_names
    assert "GREEN_AI_SHRUB_PROPOSED" in output_layer_names

    # Исходный файл не тронут (перечитываем его отдельно от output_dxf).
    input_doc_after = ezdxf.readfile(str(input_dxf))
    input_layer_names = {layer.dxf.name for layer in input_doc_after.layers}
    assert "GREEN_AI_TREE_PROPOSED" not in input_layer_names
    assert len(list(input_doc_after.modelspace())) == 3  # ровно те 3 сущности, что мы создали

    # JSON-отчёт существует, согласован со сводкой по видам посадки.
    assert output_report.exists()
    with open(output_report, encoding="utf-8") as f:
        report = json.load(f)
    assert report["territory_category"] == "dvorovye"
    assert report["summary"]["by_planting_kind"]["tree"]["placed"] == len(
        result.placement_results["tree"].placed_points()
    )
    assert report["summary"]["by_planting_kind"]["shrub"]["placed"] == len(
        result.placement_results["shrub"].placed_points()
    )


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_service_a_end_to_end_on_synthetic_object(Path(d))
    print("OK: Service A end-to-end synthetic test passed")
