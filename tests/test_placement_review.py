"""Тесты Placement Review Agent (pipeline/review/placement_review.py) —
второй режим работы (2026-09-18, прямой запрос пользователя): человек сам
решает, что и куда сажать, сервис только честно проверяет и объясняет.

Реальные каталоги (не моки) — тест находит подходящие реальные записи вместо
хардкода конкретных названий, чтобы не был хрупким к правкам исходных данных.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.placement.density import PlantingDensityRegistry
from pipeline.review.placement_review import ProposedPlanting, review_human_placements

CATALOG = DpioosSpeciesCatalog()
INVASIVE = InvasiveSpeciesRegistry()
CATEGORY = "dvorovye"


def _find_recommended_species(life_form: str) -> str:
    for entry in CATALOG.all_entries():
        if entry.life_form == life_form and entry.is_recommended_for(CATEGORY):
            return entry.name_ru
    raise AssertionError(f"Нет рекомендованного вида {life_form} для {CATEGORY} в тестовом каталоге")


def _find_invasive_dpioos_species(life_form: str) -> str:
    for entry in CATALOG.all_entries():
        if entry.life_form == life_form and INVASIVE.is_invasive(name_ru=entry.name_ru):
            return entry.name_ru
    raise AssertionError(f"Нет инвазивного вида {life_form}, присутствующего одновременно в каталоге ДПиООС")


def _find_conflicting_pair() -> tuple[str, str]:
    juniper = next(e for e in CATALOG.all_entries() if e.is_rust_fungus_alternate_host())
    fruit = next(e for e in CATALOG.all_entries() if e.is_rust_susceptible_fruit_host())
    return juniper.name_ru, fruit.name_ru


def test_legitimate_single_planting_passes():
    """Площадь намеренно маленькая (5x5 м = 25 м²): на большей площади (см.
    test_density_too_low_is_flagged) один сиротливый саженец сам по себе уже
    нарушил бы нижнюю границу нормы плотности 623-ПП — это не баг проверки,
    а реальное требование нормы, здесь площадь подобрана так, чтобы density-
    проверка не участвовала (round(lo * area_ha) == 0), проверяем только
    остальные критерии (вид/отступ)."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 5, 5)
    proposals = [ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=2.5, y=2.5)]

    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "ok"
    assert report.issues == []
    assert report.checked_count == 1


def test_density_too_low_is_flagged():
    """На большой площади один-единственный саженец занижает густоту ниже
    нижней границы 623-ПП/МГСН 1.02-02, Таблица В.1 — это не запрет, но
    честно диагностируется, а не подгоняется под "и так сойдёт"."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)  # 1 га — норма ожидает десятки деревьев
    proposals = [ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=50, y=50)]

    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    assert any(i.kind == "density_too_low" for i in report.issues)


def test_unknown_species_is_flagged():
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru="Совершенно Вымышленное Растение", life_form="tree", x=50, y=50)
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    assert any(i.kind == "unknown_species" for i in report.issues)


def test_invasive_species_is_flagged_regardless_of_category():
    invasive_tree = _find_invasive_dpioos_species("tree")
    site_boundary = box(0, 0, 100, 100)
    proposals = [ProposedPlanting(id="p1", species_name_ru=invasive_tree, life_form="tree", x=50, y=50)]

    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    assert any(i.kind == "species_invasive" for i in report.issues)


def test_species_not_recommended_for_category_is_flagged():
    tree_name = None
    for entry in CATALOG.all_entries():
        if entry.life_form == "tree" and not entry.is_recommended_for(CATEGORY):
            tree_name = entry.name_ru
            break
    assert tree_name is not None, "Нет вида дерева, НЕ рекомендованного для dvorovye, в тестовом каталоге"

    site_boundary = box(0, 0, 100, 100)
    proposals = [ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=50, y=50)]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    assert any(i.kind == "species_not_recommended_for_category" for i in report.issues)


def test_phytosanitary_conflict_between_any_pair_is_flagged():
    """Не только дерево/кустарник (как в автогенераторе) — любая пара разных
    видов из предложенного человеком списка."""
    juniper, fruit = _find_conflicting_pair()
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=juniper, life_form="shrub", x=10, y=10),
        ProposedPlanting(id="p2", species_name_ru=fruit, life_form="tree", x=90, y=90),
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    conflict_issues = [i for i in report.issues if i.kind == "species_conflict"]
    assert len(conflict_issues) == 2  # обе точки помечены


def test_insufficient_offset_from_communication_is_flagged():
    """Переиспользует ту же независимую проверку отступов, что и 6.1 — не
    доверяет решению человека так же, как не доверяла решению автогенератора."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)
    gas_line = LineString([(20, 0), (20, 100)])  # требуется 1.5 м (743-ПП)
    features = [ConstraintFeature(id="gas1", boundary_type="gas_pipeline", geometry=gas_line)]
    proposals = [ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=20.5, y=50)]  # 0.5 м

    report = review_human_placements(site_boundary, features, CATEGORY, proposals)
    assert report.verdict == "issues_found"
    assert any(i.kind == "insufficient_offset" for i in report.issues)


def test_density_too_high_is_flagged():
    tree_name = _find_recommended_species("tree")
    density_registry = PlantingDensityRegistry()
    small_boundary = box(0, 0, 10, 10)  # 100 м² — крошечная площадь
    max_count = density_registry.max_count_for_area(CATEGORY, "tree", 100.0)

    proposals = [
        ProposedPlanting(id=f"p{i}", species_name_ru=tree_name, life_form="tree", x=1.0 + i * 0.01, y=1.0)
        for i in range(max_count + 20)
    ]
    report = review_human_placements(small_boundary, [], CATEGORY, proposals, density_registry=density_registry)
    assert report.verdict == "issues_found"
    assert any(i.kind == "density_too_high" for i in report.issues)


def test_no_automatic_fix_suggested_only_explanation():
    """По прямому решению пользователя: сервис только честно объясняет, не
    предлагает конкретных исправлений ("сдвиньте точку", "уберите N штук")."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)
    gas_line = LineString([(20, 0), (20, 100)])
    features = [ConstraintFeature(id="gas1", boundary_type="gas_pipeline", geometry=gas_line)]
    proposals = [ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=20.5, y=50)]

    report = review_human_placements(site_boundary, features, CATEGORY, proposals)
    violation = next(i for i in report.issues if i.kind == "insufficient_offset")
    forbidden_phrases = ["сдвиньте", "переместите", "уберите", "добавьте"]
    assert not any(phrase in violation.detail.lower() for phrase in forbidden_phrases)


def test_estimated_cost_computed_for_known_species():
    """Прямой запрос пользователя (2026-09-20): «отсутствует примерный подсчёт
    стоимости посаженных растений при ручном вводе» — та же логика, что и в
    автогенерации (default_cost_for по классифицированной категории), просто
    по каждой точке отдельно (у ручного ввода у каждой точки может быть свой
    вид, не один общий на весь участок)."""
    from pipeline.catalog.planting_cost import PlantingCostCatalog

    tree_name = _find_recommended_species("tree")
    entry = next(e for e in CATALOG.all_entries() if e.name_ru == tree_name and e.life_form == "tree")
    expected_unit_cost = PlantingCostCatalog().default_cost_for(entry)

    site_boundary = box(0, 0, 5, 5)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=1, y=1),
        ProposedPlanting(id="p2", species_name_ru=tree_name, life_form="tree", x=3, y=3),
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.total_estimated_cost_rub == expected_unit_cost * 2
    assert report.cost_unknown_count == 0


def test_estimated_cost_excludes_unknown_species_honestly():
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 5, 5)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=1, y=1),
        ProposedPlanting(id="p2", species_name_ru="Совершенно Вымышленное Растение", life_form="tree", x=3, y=3),
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.total_estimated_cost_rub is not None
    assert report.total_estimated_cost_rub > 0
    assert report.cost_unknown_count == 1


def test_estimated_cost_is_none_for_empty_proposals():
    site_boundary = box(0, 0, 5, 5)
    report = review_human_placements(site_boundary, [], CATEGORY, [])
    assert report.total_estimated_cost_rub is None
    assert report.cost_unknown_count == 0


def test_two_trees_closer_than_group_planting_norm_are_flagged():
    """743-ПП, Таблица 3.6.2 «групповая посадка» — та же норма, что задаёт шаг
    сетки автогенератора (см. pipeline/placement/generator.py). Прямое
    замечание пользователя (2026-09-24): два дерева, перетащенных на план
    почти в одну точку, раньше не ловились НИКАКОЙ существующей проверкой —
    густота считает агрегатное число на площадь, а не расстояние между
    конкретной парой точек."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=50.0, y=50.0),
        ProposedPlanting(id="p2", species_name_ru=tree_name, life_form="tree", x=51.0, y=50.0),  # 1 м < нормы (6 м)
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert report.verdict == "issues_found"
    spacing_issues = [i for i in report.issues if i.kind == "spacing_too_close"]
    assert len(spacing_issues) == 2  # обе точки помечены, как и в species_conflict
    assert {i.planting_id for i in spacing_issues} == {"p1", "p2"}
    assert "743-ПП" in spacing_issues[0].detail


def test_two_trees_far_enough_apart_are_not_flagged_for_spacing():
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=10.0, y=50.0),
        ProposedPlanting(id="p2", species_name_ru=tree_name, life_form="tree", x=90.0, y=50.0),  # 80 м > нормы (6 м)
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert not any(i.kind == "spacing_too_close" for i in report.issues)


def test_tree_and_shrub_at_same_point_are_not_flagged_for_spacing():
    """Норма 743-ПП про расстояние ВНУТРИ одного вида посадки — дерево и
    кустарник в одной точке не считаются нарушением этой конкретной нормы
    (у них разный характер кроны/корней и разная норма отступа уже проверяется
    отдельно офсетом от коммуникаций, не друг от друга)."""
    tree_name = _find_recommended_species("tree")
    shrub_name = _find_recommended_species("shrub")
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=50.0, y=50.0),
        ProposedPlanting(id="p2", species_name_ru=shrub_name, life_form="shrub", x=50.0, y=50.0),
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    assert not any(i.kind == "spacing_too_close" for i in report.issues)


def test_three_trees_in_a_row_each_flagged_against_its_close_neighbor():
    """p1-p2 близко (1 м), p2-p3 близко (1 м), p1-p3 далеко (2 м, тоже < нормы
    6 м, так что фактически все 3 пары нарушены) — проверяем, что все три
    точки попадают в issues, а не только «соседняя» пара."""
    tree_name = _find_recommended_species("tree")
    site_boundary = box(0, 0, 100, 100)
    proposals = [
        ProposedPlanting(id="p1", species_name_ru=tree_name, life_form="tree", x=50.0, y=50.0),
        ProposedPlanting(id="p2", species_name_ru=tree_name, life_form="tree", x=51.0, y=50.0),
        ProposedPlanting(id="p3", species_name_ru=tree_name, life_form="tree", x=52.0, y=50.0),
    ]
    report = review_human_placements(site_boundary, [], CATEGORY, proposals)
    spacing_ids = {i.planting_id for i in report.issues if i.kind == "spacing_too_close"}
    assert spacing_ids == {"p1", "p2", "p3"}


if __name__ == "__main__":
    test_legitimate_single_planting_passes()
    test_density_too_low_is_flagged()
    test_unknown_species_is_flagged()
    test_invasive_species_is_flagged_regardless_of_category()
    test_species_not_recommended_for_category_is_flagged()
    test_phytosanitary_conflict_between_any_pair_is_flagged()
    test_insufficient_offset_from_communication_is_flagged()
    test_density_too_high_is_flagged()
    test_no_automatic_fix_suggested_only_explanation()
    test_estimated_cost_computed_for_known_species()
    test_estimated_cost_excludes_unknown_species_honestly()
    test_estimated_cost_is_none_for_empty_proposals()
    print("OK: placement review tests passed")
