"""Тесты Агента ОТК, проверка 6.3 — анти-галлюцинационная сверка обоснований
(pipeline/otk/citation_integrity.py).

Ключевой принцип: проверка не доверяет полю `verified`/`citation`, которое сама
же строка отчёта заявляет о себе — она пересчитывает ожидаемую цитату заново из
СВЕЖЕГО экземпляра соответствующего верифицированного реестра и сравнивает
буквально. Тесты строят JSON-отчёт вручную (а не через build_report), чтобы
намеренно смоделировать "утечку" — искажённую/подставленную цитату, как если
бы это была регрессия в коде, а не гипотетическая LLM-галлюцинация.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.otk.citation_integrity import UNVERIFIED_PLACEHOLDER, check_citation_integrity
from pipeline.placement.generator import GRID_SPACING_CITATION

REGISTRY = OffsetRegistry()
CATALOG = DpioosSpeciesCatalog()

# Реальная строка норматива building_wall (743-ПП/Таблица 3.6.1) — берётся из
# самого реестра, не переписывается вручную, чтобы тест не разъехался с данными.
BUILDING_WALL_CITATION = REGISTRY.citation("building_wall")


def _find_real_species(life_form: str, category: str):
    """Берёт первый попавшийся реальный вид каталога, рекомендованный для
    указанной категории/жизненной формы — не хардкодит конкретное название,
    чтобы тест не был хрупким к правкам исходных данных каталога."""
    for entry in CATALOG.all_entries():
        if entry.life_form == life_form and entry.is_recommended_for(category):
            return entry
    raise AssertionError(f"В тестовом каталоге нет ни одного вида для {life_form}/{category}")


def _norm_row(feature_id="f1", boundary_type="building_wall", verified=True, citation=None):
    return {
        "feature_id": feature_id,
        "boundary_type": boundary_type,
        "boundary_label_ru": "Наружная стена здания и сооружения",
        "distance_m": 5.0,
        "verified": verified,
        "citation": citation if citation is not None else (BUILDING_WALL_CITATION if verified else UNVERIFIED_PLACEHOLDER),
    }


def _placement_row(species_entry, category, planting_kind="tree", citation=None, point_id="tree_0001"):
    return {
        "id": point_id,
        "planting_kind": planting_kind,
        "status": "placed",
        "territory_category": category,
        "species_name_ru": species_entry.name_ru,
        "species_citation": citation if citation is not None else CATALOG.citation_for(species_entry, category),
        "grid_spacing_m": 6.0,
        "grid_spacing_citation": GRID_SPACING_CITATION,
    }


def test_legitimate_report_passes():
    entry = _find_real_species("tree", "dvorovye")
    report = {
        "applied_constraint_norms_by_kind": {"tree": [_norm_row()]},
        "placements": [_placement_row(entry, "dvorovye")],
    }
    result = check_citation_integrity(report)
    assert result.verdict == "ok"
    assert result.violations == []


def test_unverified_row_with_honest_placeholder_passes():
    report = {
        "applied_constraint_norms_by_kind": {"tree": [_norm_row(verified=False)]},
        "placements": [],
    }
    result = check_citation_integrity(report)
    assert result.verdict == "ok"


def test_unverified_row_with_substantive_text_is_flagged():
    """Ключевой анти-галлюцинационный сценарий: строка честно помечена
    verified=False, но вместо плейсхолдера в неё подставлен настоящий текст —
    это именно то, что 6.3 обязана ловить."""
    report = {
        "applied_constraint_norms_by_kind": {
            "tree": [_norm_row(verified=False, citation="Придуманная норма, п. 5.5")]
        },
        "placements": [],
    }
    result = check_citation_integrity(report)
    assert result.verdict == "violation"
    assert result.violations[0].kind == "unverified_row_has_substantive_text"


def test_tampered_offset_citation_is_flagged():
    """Цитата верифицированной строки не совпадает с тем, что реально выдаёт
    OffsetRegistry для этого boundary_type — например, копипаста не того текста."""
    report = {
        "applied_constraint_norms_by_kind": {
            "tree": [_norm_row(citation="ППМ № 743-ПП — «Ось трамвайных путей» (не та строка)")]
        },
        "placements": [],
    }
    result = check_citation_integrity(report)
    assert result.verdict == "violation"
    assert result.violations[0].kind == "unrecognized_citation"


def test_tampered_species_citation_is_flagged():
    entry = _find_real_species("shrub", "parki")
    report = {
        "applied_constraint_norms_by_kind": {},
        "placements": [_placement_row(entry, "parki", planting_kind="shrub", citation="Вымышленное обоснование вида")],
    }
    result = check_citation_integrity(report)
    assert result.verdict == "violation"
    assert result.violations[0].kind == "species_citation_mismatch"


def test_tampered_grid_spacing_citation_is_flagged():
    entry = _find_real_species("tree", "dvorovye")
    row = _placement_row(entry, "dvorovye")
    row["grid_spacing_citation"] = "Придуманный норматив шага посадки"
    report = {"applied_constraint_norms_by_kind": {}, "placements": [row]}
    result = check_citation_integrity(report)
    assert result.verdict == "violation"
    assert result.violations[0].kind == "grid_spacing_citation_mismatch"


def test_species_citation_without_species_name_is_flagged():
    row = {
        "id": "tree_0002",
        "planting_kind": "tree",
        "status": "no_recommended_species",
        "territory_category": "dvorovye",
        "species_name_ru": None,
        "species_citation": "Обоснование без вида",
        "grid_spacing_m": 6.0,
        "grid_spacing_citation": GRID_SPACING_CITATION,
    }
    report = {"applied_constraint_norms_by_kind": {}, "placements": [row]}
    result = check_citation_integrity(report)
    assert result.verdict == "violation"
    assert result.violations[0].kind == "species_citation_without_species"


def test_no_recommended_species_point_with_no_citation_passes():
    row = {
        "id": "tree_0002",
        "planting_kind": "tree",
        "status": "no_recommended_species",
        "territory_category": "dvorovye",
        "species_name_ru": None,
        "species_citation": None,
        "grid_spacing_m": 6.0,
        "grid_spacing_citation": GRID_SPACING_CITATION,
    }
    report = {"applied_constraint_norms_by_kind": {}, "placements": [row]}
    result = check_citation_integrity(report)
    assert result.verdict == "ok"


if __name__ == "__main__":
    test_legitimate_report_passes()
    test_unverified_row_with_honest_placeholder_passes()
    test_unverified_row_with_substantive_text_is_flagged()
    test_tampered_offset_citation_is_flagged()
    test_tampered_species_citation_is_flagged()
    test_tampered_grid_spacing_citation_is_flagged()
    test_species_citation_without_species_name_is_flagged()
    test_no_recommended_species_point_with_no_citation_passes()
    print("OK: citation integrity (Agent OTK 6.3) tests passed")
