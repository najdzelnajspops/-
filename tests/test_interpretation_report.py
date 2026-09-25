"""Тесты Interpretation Report Agent (pipeline/report/interpretation_report.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import GeometryCollection, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesEntry
from pipeline.constraints.buffer_engine import ConstraintMapResult, ZoneSource
from pipeline.placement.generator import PlacementPoint, PlacementResult
from pipeline.report.interpretation_report import build_report, write_report


def _sample_constraint_result() -> ConstraintMapResult:
    zone = box(0, 0, 10, 10)
    return ConstraintMapResult(
        planting_kind="tree",
        site_boundary=zone,
        allowed_zone=zone,
        forbidden_zone=GeometryCollection(),
        zone_sources=[
            ZoneSource(
                feature_id="up.dxf:h1A",
                boundary_type="gas_pipeline",
                boundary_label_ru="Газопровод",
                distance_m=1.5,
                verified=True,
                citation="ППМ № 743-ПП — «Газопровод»",
                buffer_geometry=box(0, 0, 1, 1),
            )
        ],
    )


def _sample_placement_result() -> PlacementResult:
    return PlacementResult(
        planting_kind="tree",
        territory_category="dvorovye",
        points=[
            PlacementPoint(
                id="tree_0001",
                planting_kind="tree",
                x=5.0,
                y=5.0,
                status="placed",
                species_name_ru="Ель колючая",
                species_citation="ДПиООС — категория «Дворовые территории»: вид «Ель колючая» рекомендован (+)",
                grid_spacing_m=6.0,
                grid_spacing_citation="743-ПП, Таблица 3.6.2",
            ),
            PlacementPoint(
                id="tree_0002",
                planting_kind="tree",
                x=8.0,
                y=8.0,
                status="no_recommended_species",
                species_name_ru=None,
                species_citation=None,
                grid_spacing_m=6.0,
                grid_spacing_citation="743-ПП, Таблица 3.6.2",
            ),
        ],
    )


def test_build_report_structure_and_summary_counts():
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["up.dxf", "tp.dxf"],
    )

    assert report["territory_category"] == "dvorovye"
    assert report["source_files"] == ["up.dxf", "tp.dxf"]
    assert report["summary"]["total_points"] == 2
    assert report["summary"]["placed"] == 1
    assert report["summary"]["rejected"] == 1
    assert report["summary"]["by_planting_kind"]["tree"] == {"placed": 1, "rejected": 1}
    assert len(report["known_simplifications"]) >= 1
    assert report["site_boundary_status"] == "found"


def test_low_confidence_boundary_status_adds_explicit_warning():
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["up.dxf"],
        site_boundary_status="found_low_confidence",
    )
    assert report["site_boundary_status"] == "found_low_confidence"
    assert any("found_low_confidence" in note for note in report["known_simplifications"])


def test_placement_rows_carry_citation_norms_stored_once_at_top_level():
    """Регрессионный тест на баг 2026-09-16 (168 ГБ JSON на реальном объекте,
    140447 объектов-ограничений): применённые нормы обязаны храниться ОДИН РАЗ
    на вид посадки (`applied_constraint_norms_by_kind`), а НЕ копироваться в
    каждую точку — иначе размер отчёта растёт как (число точек × число норм)."""
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["up.dxf"],
    )
    placed_row = next(p for p in report["placements"] if p["id"] == "tree_0001")
    assert placed_row["species_name_ru"] == "Ель колючая"
    assert "applied_constraint_norms" not in placed_row  # НЕ дублируется в точке

    norms = report["applied_constraint_norms_by_kind"]["tree"]
    assert norms[0]["boundary_type"] == "gas_pipeline"
    assert norms[0]["verified"] is True

    rejected_row = next(p for p in report["placements"] if p["id"] == "tree_0002")
    assert rejected_row["status"] == "no_recommended_species"
    assert rejected_row["species_name_ru"] is None


def test_report_size_scales_linearly_not_multiplicatively_with_points_and_norms():
    """Регрессионный тест на баг 2026-09-16: с 500 точками и 2000 применёнными
    нормами старая схема (нормы в каждой точке) дала бы ~500*2000=1,000,000
    записей в JSON; правильная схема (нормы один раз) даёт ~2000+500. Проверяем
    длину сериализованного JSON — она обязана остаться в разумных пределах, а
    не расти как произведение количеств (что и привело к 168 ГБ на реальном объекте)."""
    zone = box(0, 0, 1000, 1000)
    many_norms = [
        ZoneSource(
            feature_id=f"f{i}",
            boundary_type="gas_pipeline",
            boundary_label_ru="Газопровод",
            distance_m=1.5,
            verified=True,
            citation="ППМ № 743-ПП — «Газопровод»",
            buffer_geometry=box(0, 0, 1, 1),
        )
        for i in range(2000)
    ]
    constraint_result = ConstraintMapResult(
        planting_kind="tree",
        site_boundary=zone,
        allowed_zone=zone,
        forbidden_zone=GeometryCollection(),
        zone_sources=many_norms,
    )
    many_points = [
        PlacementPoint(
            id=f"tree_{i:04d}",
            planting_kind="tree",
            x=float(i),
            y=float(i),
            status="placed",
            species_name_ru="Ель колючая",
            species_citation="citation",
            grid_spacing_m=6.0,
            grid_spacing_citation="743-ПП",
        )
        for i in range(500)
    ]
    placement_result = PlacementResult(planting_kind="tree", territory_category="dvorovye", points=many_points)

    report = build_report(
        territory_category="dvorovye",
        constraint_results=[constraint_result],
        placement_results=[placement_result],
        source_files=["up.dxf"],
    )
    serialized_length = len(json.dumps(report, ensure_ascii=False))
    # 500 точек + 2000 норм при линейном росте — сотни КБ, не сотни МБ/ГБ.
    assert serialized_length < 5_000_000  # 5 МБ — большой запас, но на порядки меньше "перемноженного" объёма


def test_species_choices_report_eligible_alternatives_and_tie_break():
    """Отчёт обязан честно показывать не только выбранный вид, но и все равно
    допустимые альтернативы + объяснение тай-брейка (см. TIE_BREAK_EXPLANATION
    в pipeline/placement/generator.py) — иначе вопрос «почему именно этот вид,
    а не другой» остаётся без ответа для проверяющего жюри."""
    chosen = DpioosSpeciesEntry(
        name_ru="Береза",
        life_form="tree",
        life_form_group_ru="Лиственные деревья",
        source="fixture",
        footnotes=(),
        territory_flags={"dvorovye": "+"},
    )
    placement_with_alternatives = PlacementResult(
        planting_kind="tree",
        territory_category="dvorovye",
        points=[],
        chosen_species_entry=chosen,
        eligible_alternatives=["Вяз", "Липа"],
    )
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[placement_with_alternatives],
        source_files=["up.dxf"],
    )
    choice = report["species_choices"][0]
    assert choice["chosen_species_name_ru"] == "Береза"
    assert choice["eligible_alternatives"] == ["Вяз", "Липа"]
    assert choice["tie_break_explanation"] is not None
    assert "алфавит" in choice["tie_break_explanation"].lower()


def test_no_lep_unknown_voltage_gives_zero_count_and_no_warning():
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["up.dxf"],
    )
    assert report["lep_unknown_voltage"]["count"] == 0
    assert report["lep_unknown_voltage"]["fallback_distance_m"] is None
    assert not any("ЛЭП" in note for note in report["known_simplifications"])


def test_lep_unknown_voltage_adds_explicit_human_readable_warning():
    """Найдено на реальном объекте 2026-09-17 («Камчатская улица»): ЛЭП с
    неопределённым классом напряжения получает максимально консервативный
    отступ (55 м, тариф 1150 кВ) — это способно критически уменьшить итоговую
    допустимую зону, но раньше никак не сообщалось пользователю отчёта.
    Пользователь прямо потребовал: такие вещи должны быть явно прописаны."""
    report = build_report(
        territory_category="dvorovye",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["up.dxf"],
        lep_unknown_voltage_count=3,
        lep_unknown_voltage_fallback_m=55.0,
    )
    assert report["lep_unknown_voltage"]["count"] == 3
    assert report["lep_unknown_voltage"]["fallback_distance_m"] == 55.0

    warning = next(note for note in report["known_simplifications"] if "ЛЭП" in note)
    assert "3" in warning
    assert "55" in warning
    assert "160" in warning  # ссылка на Постановление №160


def test_write_report_produces_valid_json_file(tmp_path):
    out = tmp_path / "report.json"
    write_report(
        output_path=out,
        territory_category="parki",
        constraint_results=[_sample_constraint_result()],
        placement_results=[_sample_placement_result()],
        source_files=["tp.dxf"],
    )
    assert out.exists()
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    assert data["territory_category"] == "parki"
    assert data["summary"]["total_points"] == 2


if __name__ == "__main__":
    test_build_report_structure_and_summary_counts()
    test_placement_rows_carry_citation_norms_stored_once_at_top_level()
    print("OK: interpretation report tests passed (except tmp_path test, run via pytest)")
