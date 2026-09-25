"""Тесты MVP Placement Generator (pipeline/placement/generator.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import GeometryCollection, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.placement.generator import (
    GRID_SPACING_CITATION,
    ClusterConfig,
    generate_placement,
)


def _make_constraint_result(planting_kind: str, allowed_zone) -> ConstraintMapResult:
    return ConstraintMapResult(
        planting_kind=planting_kind,
        site_boundary=allowed_zone,
        allowed_zone=allowed_zone,
        forbidden_zone=GeometryCollection(),
        zone_sources=[],
        skipped_features=[],
        unknown_boundary_types=set(),
    )


def test_grid_spacing_uses_verified_743pp_table_and_strict_containment():
    """Квадрат 12x12: шаг сетки для дерева = (5+7)/2 = 6 м (743-ПП, Таблица 3.6.2,
    «групповая посадка деревьев»). Узлы сетки 0/6/12 по X и Y — только (6,6) лежит
    строго внутри полигона (contains исключает границу), остальные 8 узлов — на
    границе/в углах, отбрасываются."""
    allowed = box(0, 0, 12, 12)
    result = _make_constraint_result("tree", allowed)
    registry = OffsetRegistry()

    placement = generate_placement(
        constraint_result=result,
        territory_category="dvorovye",
        life_form="tree",
        species_catalog=DpioosSpeciesCatalog(),
        invasive_registry=InvasiveSpeciesRegistry(),
        planting_density=registry.planting_density,
    )

    placed = placement.placed_points()
    assert len(placed) == 1
    assert (placed[0].x, placed[0].y) == (6.0, 6.0)
    assert placed[0].grid_spacing_m == 6.0
    assert placed[0].grid_spacing_citation == GRID_SPACING_CITATION
    assert placed[0].species_name_ru is not None
    assert placed[0].species_citation is not None


def test_species_choice_is_deterministic_alphabetical_and_not_invasive():
    """Повторный прогон с тем же входом обязан выбрать тот же вид (детерминированность,
    docs/ARCHITECTURE.md §3.1), и этот вид не должен быть в перечне 369-ПП."""
    allowed = box(0, 0, 12, 12)
    result = _make_constraint_result("tree", allowed)
    registry = OffsetRegistry()
    catalog = DpioosSpeciesCatalog()
    invasive = InvasiveSpeciesRegistry()

    runs = [
        generate_placement(result, "dvorovye", "tree", catalog, invasive, registry.planting_density)
        for _ in range(3)
    ]
    species_names = {r.placed_points()[0].species_name_ru for r in runs}
    assert len(species_names) == 1
    chosen_name = species_names.pop()
    assert not invasive.is_invasive(name_ru=chosen_name)

    all_recommended = sorted(
        e.name_ru for e in catalog.recommended_for("dvorovye", "tree") if not invasive.is_invasive(name_ru=e.name_ru)
    )
    assert chosen_name == all_recommended[0]


def test_empty_allowed_zone_produces_no_points():
    result = _make_constraint_result("shrub", GeometryCollection())
    registry = OffsetRegistry()
    placement = generate_placement(
        result, "parki", "shrub", DpioosSpeciesCatalog(), InvasiveSpeciesRegistry(), registry.planting_density
    )
    assert placement.points == []


def test_no_recommended_species_is_flagged_honestly_not_guessed(tmp_path):
    """Если единственный «+»-вид для категории/формы — инвазивный (369-ПП), после
    фильтрации не остаётся ни одного кандидата: точка обязана получить статус
    `no_recommended_species`, а не произвольный вид."""
    fixture = {
        "source_documents": {"fixture": "тестовый фикстур"},
        "territory_categories": {"dvorovye": "Дворовые территории"},
        "footnote_legend": {},
        "species": [
            {
                "source": "fixture",
                "life_form": "Лиственные деревья",
                "name": "Клен ясенелистный",
                "footnotes": [],
                "territory_flags": {"dvorovye": "+"},
            }
        ],
    }
    fixture_path = tmp_path / "only_invasive.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    allowed = box(0, 0, 12, 12)
    result = _make_constraint_result("tree", allowed)
    registry = OffsetRegistry()

    placement = generate_placement(
        result,
        "dvorovye",
        "tree",
        DpioosSpeciesCatalog(path=fixture_path),
        InvasiveSpeciesRegistry(),
        registry.planting_density,
    )

    assert len(placement.points) == 1
    point = placement.points[0]
    assert point.status == "no_recommended_species"
    assert point.species_name_ru is None
    assert point.species_citation is None
    assert placement.rejected_points() == [point]
    assert placement.placed_points() == []


def _write_conflict_fixture(tmp_path) -> Path:
    """Дерево = яблоня (восприимчивый хозяин), кустарники = можжевельник
    (переносчик ржавчинных грибов, сноска [6], алфавитно первый кандидат) и
    сирень (без конфликта, алфавитно второй) — ожидаем, что можжевельник
    будет пропущен из-за конфликта с уже выбранной яблоней, выбрана сирень."""
    fixture = {
        "source_documents": {"fixture": "тестовый фикстур"},
        "territory_categories": {"dvorovye": "Дворовые территории"},
        "footnote_legend": {
            "6": "Не высаживать вблизи плодовых культур (яблоня, груша, айва) из-за риска распространения ржавчинных грибов"
        },
        "species": [
            {
                "source": "fixture",
                "life_form": "Лиственные деревья",
                "name": "Яблоня Тестовая",
                "footnotes": [],
                "territory_flags": {"dvorovye": "+"},
            },
            {
                "source": "fixture",
                "life_form": "Хвойные кустарники",
                "name": "Можжевельник Тестовый",
                "footnotes": [6],
                "territory_flags": {"dvorovye": "+"},
            },
            {
                "source": "fixture",
                "life_form": "Лиственные кустарники",
                "name": "Сирень Тестовая",
                "footnotes": [],
                "territory_flags": {"dvorovye": "+"},
            },
        ],
    }
    fixture_path = tmp_path / "conflict_fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return fixture_path


def test_shrub_species_choice_avoids_phytosanitary_conflict_with_chosen_tree(tmp_path):
    fixture_path = _write_conflict_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    tree_result = generate_placement(
        _make_constraint_result("tree", allowed), "dvorovye", "tree", catalog, invasive, registry.planting_density
    )
    assert tree_result.chosen_species_entry.name_ru == "Яблоня Тестовая"

    shrub_result = generate_placement(
        _make_constraint_result("shrub", allowed),
        "dvorovye",
        "shrub",
        catalog,
        invasive,
        registry.planting_density,
        avoid_conflict_with=tree_result.chosen_species_entries,
    )

    assert shrub_result.chosen_species_entry.name_ru == "Сирень Тестовая"  # не можжевельник
    assert shrub_result.species_conflict_note is not None
    assert "Можжевельник Тестовый" in shrub_result.species_conflict_note


def test_cluster_config_produces_small_groups_not_dense_grid(tmp_path):
    """Регрессионный тест на баг 2026-09-16 (292932 куста на 49 га): с заданным
    cluster_config точки идут небольшими группами (шаг 0.3 м внутри), а не
    сплошной сеткой по всей площади — итоговое число далеко от того, что дала
    бы старая сплошная сетка 0.3 м на той же площади."""
    fixture_path = _write_conflict_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 100, 100)  # 1 га

    cluster_config = ClusterConfig(group_size=7, group_spacing_m=15.0, intra_group_spacing_m=0.3)
    result = generate_placement(
        _make_constraint_result("shrub", allowed),
        "dvorovye",
        "shrub",
        catalog,
        invasive,
        registry.planting_density,
        cluster_config=cluster_config,
    )

    old_dense_grid_count = ((100 // 0.3) + 1) ** 2  # старая (неправильная) сплошная сетка на той же площади
    assert len(result.points) < old_dense_grid_count / 10  # на порядок меньше, не сопоставимо
    assert len(result.points) > 0
    for point in result.placed_points():
        assert point.grid_spacing_m == 0.3  # записан шаг ВНУТРИ группы, не между группами


def test_species_choice_without_avoid_conflict_picks_alphabetically_first(tmp_path):
    """Без avoid_conflict_with (например, дерево — первое в обработке) конфликт
    не проверяется вообще: можжевельник, будучи алфавитно первым кустарником,
    просто выбирается, заметки о конфликте нет."""
    fixture_path = _write_conflict_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    shrub_result = generate_placement(
        _make_constraint_result("shrub", allowed), "dvorovye", "shrub", catalog, invasive, registry.planting_density
    )
    assert shrub_result.chosen_species_entry.name_ru == "Можжевельник Тестовый"
    assert shrub_result.species_conflict_note is None
    assert shrub_result.eligible_alternatives == ["Сирень Тестовая"]  # честно показан равно допустимый кандидат


def test_eligible_alternatives_lists_all_other_valid_candidates(tmp_path):
    """Помимо выбранного вида, отчёт обязан честно перечислять ВСЕ остальные
    равно допустимые кандидаты (см. TIE_BREAK_EXPLANATION) — не только назвать
    один выбранный вид, будто альтернатив не было."""
    fixture = {
        "source_documents": {"fixture": "тестовый фикстур"},
        "territory_categories": {"dvorovye": "Дворовые территории"},
        "footnote_legend": {},
        "species": [
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Береза",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Вяз",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Липа",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
        ],
    }
    fixture_path = tmp_path / "alternatives_fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    result = generate_placement(
        _make_constraint_result("tree", allowed), "dvorovye", "tree", catalog, invasive, registry.planting_density
    )
    assert result.chosen_species_entry.name_ru == "Береза"  # первая по алфавиту
    assert result.eligible_alternatives == ["Вяз", "Липа"]  # остальные две — честно показаны, не скрыты


def _write_three_species_fixture(tmp_path) -> Path:
    fixture = {
        "source_documents": {"fixture": "тестовый фикстур"},
        "territory_categories": {"dvorovye": "Дворовые территории"},
        "footnote_legend": {},
        "species": [
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Береза",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Вяз",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
            {"source": "fixture", "life_form": "Лиственные деревья", "name": "Липа",
             "footnotes": [], "territory_flags": {"dvorovye": "+"}},
        ],
    }
    fixture_path = tmp_path / "three_species_fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return fixture_path


def test_selected_species_names_none_keeps_single_species_behavior(tmp_path):
    """Прямое требование обратной совместимости (2026-09-24, чек-боксы видов):
    без selected_species_names поведение не должно измениться ни на бит —
    один и тот же вид на КАЖДОЙ точке, как раньше."""
    fixture_path = _write_three_species_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 30, 24)  # достаточно точек-кандидатов на шаге 6 м, чтобы round-robin было видно

    result = generate_placement(
        _make_constraint_result("tree", allowed), "dvorovye", "tree", catalog, invasive, registry.planting_density
    )
    placed = result.placed_points()
    assert len(placed) > 3  # содержательный сценарий, не вырожденный
    assert {p.species_name_ru for p in placed} == {"Береза"}
    assert result.chosen_species_entries == [result.chosen_species_entry]


def test_selected_species_names_distributes_round_robin_alphabetically(tmp_path):
    """Прямой запрос пользователя (2026-09-24): «список растений с чек-боксами,
    чтобы пользователь мог сначала задать перечень растений» — несколько
    выбранных видов должны реально использоваться, не только первый по
    алфавиту. Round-robin в алфавитном порядке — детерминированно, без seed."""
    fixture_path = _write_three_species_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 30, 24)

    result = generate_placement(
        _make_constraint_result("tree", allowed),
        "dvorovye",
        "tree",
        catalog,
        invasive,
        registry.planting_density,
        selected_species_names=["Липа", "Береза", "Вяз"],  # порядок ввода не имеет значения
    )
    placed = result.placed_points()
    assert len(placed) > 3
    used_species = {p.species_name_ru for p in placed}
    assert used_species == {"Береза", "Вяз", "Липа"}  # ВСЕ три реально использованы, не только первый
    # round-robin строго по алфавиту, начиная с первой точки
    ordered = ["Береза", "Вяз", "Липа"]
    for i, p in enumerate(placed):
        assert p.species_name_ru == ordered[i % 3]
    assert result.chosen_species_entries[0].name_ru == "Береза"  # первый элемент — по-прежнему алфавитный представитель
    assert len(result.chosen_species_entries) == 3


def test_selected_species_names_excludes_invasive_even_if_selected(tmp_path):
    """Чек-бокс не должен позволить обойти запрет 369-ПП — сервис перепроверяет
    инвазивность сам, не доверяя входу молча (тот же принцип, что и везде в
    проекте: не полагаться на то, что вызывающий код уже всё проверил)."""
    invasive_tree = next(
        e.name_ru
        for e in DpioosSpeciesCatalog().all_entries()
        if e.life_form == "tree" and InvasiveSpeciesRegistry().is_invasive(name_ru=e.name_ru)
    )
    non_invasive_tree = next(
        e.name_ru
        for e in DpioosSpeciesCatalog().recommended_for("dvorovye", "tree")
        if not InvasiveSpeciesRegistry().is_invasive(name_ru=e.name_ru)
    )
    catalog = DpioosSpeciesCatalog()
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    result = generate_placement(
        _make_constraint_result("tree", allowed),
        "dvorovye",
        "tree",
        catalog,
        invasive,
        registry.planting_density,
        selected_species_names=[invasive_tree, non_invasive_tree],
    )
    used_names = {p.species_name_ru for p in result.placed_points()}
    assert invasive_tree not in used_names
    assert non_invasive_tree in used_names
    assert result.species_conflict_note is not None
    assert invasive_tree in result.species_conflict_note


def test_selected_species_names_excludes_cross_kind_conflict(tmp_path):
    """Многовидовой режим обязан применять ТУ ЖЕ фитосанитарную проверку
    (сноска [6]), что и однвидовой — можжевельник в выбранном наборе кустарника
    исключается, если дерево уже занимает конфликтующую яблоню."""
    fixture_path = _write_conflict_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    tree_result = generate_placement(
        _make_constraint_result("tree", allowed), "dvorovye", "tree", catalog, invasive, registry.planting_density
    )
    assert tree_result.chosen_species_entry.name_ru == "Яблоня Тестовая"

    shrub_result = generate_placement(
        _make_constraint_result("shrub", allowed),
        "dvorovye",
        "shrub",
        catalog,
        invasive,
        registry.planting_density,
        avoid_conflict_with=tree_result.chosen_species_entries,
        selected_species_names=["Можжевельник Тестовый", "Сирень Тестовая"],
    )
    used_names = {p.species_name_ru for p in shrub_result.placed_points()}
    assert used_names == {"Сирень Тестовая"}
    assert shrub_result.species_conflict_note is not None
    assert "Можжевельник Тестовый" in shrub_result.species_conflict_note


def test_selected_species_names_all_excluded_gives_honest_no_recommended_species(tmp_path):
    """Если после фильтров (369-ПП/конфликт) не осталось ни одного вида —
    точки честно получают no_recommended_species, как и в однвидовом режиме,
    а не падение/пустой результат без объяснения."""
    fixture_path = _write_conflict_fixture(tmp_path)
    catalog = DpioosSpeciesCatalog(path=fixture_path)
    invasive = InvasiveSpeciesRegistry()
    registry = OffsetRegistry()
    allowed = box(0, 0, 12, 12)

    tree_result = generate_placement(
        _make_constraint_result("tree", allowed), "dvorovye", "tree", catalog, invasive, registry.planting_density
    )

    shrub_result = generate_placement(
        _make_constraint_result("shrub", allowed),
        "dvorovye",
        "shrub",
        catalog,
        invasive,
        registry.planting_density,
        avoid_conflict_with=tree_result.chosen_species_entries,
        selected_species_names=["Можжевельник Тестовый"],  # единственный выбранный — конфликтует
    )
    assert shrub_result.placed_points() == []
    assert all(p.status == "no_recommended_species" for p in shrub_result.points)
    assert shrub_result.chosen_species_entries == []


if __name__ == "__main__":
    test_grid_spacing_uses_verified_743pp_table_and_strict_containment()
    test_species_choice_is_deterministic_alphabetical_and_not_invasive()
    test_empty_allowed_zone_produces_no_points()
    print("OK: placement generator tests passed (except tmp_path test, run via pytest)")
