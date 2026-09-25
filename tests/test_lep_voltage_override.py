"""Ручное указание класса напряжения ЛЭП оператором (2026-09-25, прямой запрос
пользователя — «человек имеет дополнительные материалы на руках и может
самостоятельно определить класс объекта — сервис должен пересчитать параметры
участка»).

Без этого override ЛЭП с неопределённым по чертежу классом напряжения получает
САМЫЙ ШИРОКИЙ консервативный отступ (55 м, тариф 1150 кВ, см.
pipeline/constraints/lep_zones.py::most_conservative_width_m) — на практике
способный занять почти весь участок (см. docs/OPEN_QUESTIONS.md, случай
«Камчатская улица»). Тесты здесь проверяют и юнит-логику override
(pipeline/constraints/lep_voltage_override.py), и то, что она реально доходит
до Constraint Engine через run_service_a (сквозной путь, не только изолированная
функция).
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.lep_voltage_override import apply_lep_voltage_override
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.run import run_service_a

UNKNOWN = "power_line_overhead_unknown_voltage"


def _lep_feature(fid: str) -> ConstraintFeature:
    registry = LepZoneRegistry()
    return ConstraintFeature(
        id=fid,
        boundary_type=UNKNOWN,
        geometry=LineString([(0, 0), (10, 0)]),
        label="ЛЭП (класс напряжения не определён по данным чертежа)",
        explicit_distance_m=registry.most_conservative_width_m(),
        explicit_citation=None,
    )


def _other_feature(fid: str) -> ConstraintFeature:
    return ConstraintFeature(id=fid, boundary_type="gas_pipeline", geometry=LineString([(0, 0), (10, 0)]))


def test_override_none_leaves_features_unchanged():
    features = [_lep_feature("lep-1"), _other_feature("gas-1")]
    result = apply_lep_voltage_override(features, None, LepZoneRegistry())
    assert result == features


def test_override_replaces_distance_and_citation_only_for_unknown_voltage_lep():
    features = [_lep_feature("lep-1"), _other_feature("gas-1")]
    registry = LepZoneRegistry()
    result = apply_lep_voltage_override(features, 10.0, registry)

    lep_out = next(f for f in result if f.id == "lep-1")
    gas_out = next(f for f in result if f.id == "gas-1")

    assert lep_out.explicit_distance_m == 10.0  # тариф 1-20 кВ по ПП РФ №160
    assert lep_out.explicit_citation is not None
    assert "10 кВ" in lep_out.explicit_citation
    assert "вручную" in lep_out.explicit_citation

    # Другой тип фичи не тронут вообще (тот же объект, не копия с теми же полями).
    assert gas_out is features[1]


def test_override_above_known_tariff_raises_instead_of_silently_falling_back():
    features = [_lep_feature("lep-1")]
    with pytest.raises(ValueError):
        apply_lep_voltage_override(features, 1500.0, LepZoneRegistry())


def _make_object_with_lep(path: Path) -> None:
    """Небольшой синтетический участок с одной ЛЭП неизвестного класса, пересекающей
    весь участок — при консервативном отступе (55 м) должна съесть допустимую зону
    почти полностью; при явном низковольтном классе — оставить участок почти целым."""
    doc = ezdxf.new(setup=False)
    for layer in ("Граница площадки", "ЛЭП"):
        doc.layers.add(name=layer)
    msp = doc.modelspace()

    boundary = msp.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)], dxfattribs={"layer": "Граница площадки"}
    )
    boundary.closed = True
    msp.add_line((50, -10), (50, 110), dxfattribs={"layer": "ЛЭП"})

    doc.saveas(str(path))


def test_run_service_a_without_override_shrinks_allowed_zone_severely(tmp_path):
    input_dxf = tmp_path / "object.dxf"
    _make_object_with_lep(input_dxf)

    result = run_service_a(
        input_dxf_paths=[input_dxf],
        base_dxf_path_for_export=input_dxf,
        territory_category="dvorovye",
        output_dxf_path=tmp_path / "out.dxf",
        output_report_path=tmp_path / "report.json",
    )

    site_area = result.ingest_result.site_boundary.area
    tree_allowed_area = result.constraint_results["tree"].allowed_zone.area
    # Отступ 55 м в обе стороны от линии внутри участка 100х100 не оставляет
    # почти ничего допустимого.
    assert tree_allowed_area < 0.05 * site_area
    assert result.lep_voltage_override_kv is None
    assert result.report["lep_unknown_voltage"]["operator_override_kv"] is None
    assert result.report["lep_unknown_voltage"]["applied_distance_m"] == 55.0


def test_run_service_a_with_operator_voltage_override_recovers_allowed_zone(tmp_path):
    input_dxf = tmp_path / "object.dxf"
    _make_object_with_lep(input_dxf)

    result = run_service_a(
        input_dxf_paths=[input_dxf],
        base_dxf_path_for_export=input_dxf,
        territory_category="dvorovye",
        output_dxf_path=tmp_path / "out.dxf",
        output_report_path=tmp_path / "report.json",
        lep_voltage_kv_override=10.0,  # тариф 1-20 кВ -> 10 м вместо 55 м
    )

    site_area = result.ingest_result.site_boundary.area
    tree_allowed_area = result.constraint_results["tree"].allowed_zone.area
    # Отступ 10 м в обе стороны от центральной линии на участке 100х100
    # оставляет заметно больше половины площади допустимой.
    assert tree_allowed_area > 0.5 * site_area

    assert result.lep_voltage_override_kv == 10.0
    assert result.report["lep_unknown_voltage"]["operator_override_kv"] == 10.0
    assert result.report["lep_unknown_voltage"]["applied_distance_m"] == 10.0
    assert result.report["lep_unknown_voltage"]["fallback_distance_m"] == 55.0  # что было бы по умолчанию
    # Честность отчёта: known_simplifications должен явно называть это
    # человеческим решением, а не автоматическим определением по чертежу.
    joined = " ".join(result.report["known_simplifications"])
    assert "оператор" in joined.lower()
    assert "10" in joined


if __name__ == "__main__":
    import tempfile

    test_override_none_leaves_features_unchanged()
    test_override_replaces_distance_and_citation_only_for_unknown_voltage_lep()

    with tempfile.TemporaryDirectory() as d:
        test_run_service_a_without_override_shrinks_allowed_zone_severely(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_run_service_a_with_operator_voltage_override_recovers_allowed_zone(Path(d))
    print("OK: LEP voltage override tests passed")
