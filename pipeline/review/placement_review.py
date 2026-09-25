"""Placement Review Agent — проверка ЧЕЛОВЕЧЕСКОГО выбора посадки (Service A/B).

Прямой запрос пользователя (2026-09-18): «ЧЕЛОВЕК будет решать, что и где
сажать, а ты будешь проверять и анализировать, можно ли так посадить растения
или нужно увеличить отступы/увеличить плотность и так далее». Второй режим
работы, ДОБАВЛЯЕТСЯ рядом с автоматическим Placement Generator (`pipeline/
placement/generator.py`), не заменяет его — оба остаются доступны.

Вход — произвольный список `ProposedPlanting` (вид + жизненная форма + точка),
предложенный человеком, БЕЗ ПРИВЯЗКИ к тому, как он получен (DXF-слой, форма
в UI, CSV — не важно, парсинг входа вне этого модуля). Дальше — только честная
проверка и объяснение (по прямому решению пользователя: без автопредложений
исправлений — «сдвиньте точку на 2 м» и т.п. НЕ входит в эту проверку, только
диагноз и ссылка на норму):

1. **Соответствие каталогу ДПиООС** — вид вообще существует в справочнике
   (`data/reference/dpioos_species_assortment.json`, 329 видов из двух
   присланных пользователем официальных файлов mos.ru); если существует —
   рекомендован ли для указанной категории территории и жизненной формы;
   инвазивен ли (369-ПП, абсолютный запрет вне зависимости от категории).
2. **Фитосанитарный конфликт** (сноска [6] ДПиООС) — между ЛЮБОЙ парой РАЗНЫХ
   видов из предложенного человеком списка (не только «дерево против
   кустарника», как в автогенераторе — человек может предложить сколько
   угодно разных видов на одном участке).
3. **Отступы от коммуникаций** (743-ПП) — переиспользует ту же независимую
   проверку, что и агент ОТК 6.1 (`pipeline/otk/offset_compliance.py`), без
   каких-либо изменений — она уже устроена так, чтобы не доверять ничьему
   решению о размещении, включая теперь и решение человека, а не только
   автогенератора.
4. **Густота посадки** (623-ПП/МГСН 1.02-02, Таблица В.1) — предложенное
   человеком общее число деревьев/кустарников сравнивается с диапазоном нормы
   на фактическую допустимую площадь; отдельно "слишком густо" и "слишком
   редко" (агент ОТК/автогенератор проверяли только верхнюю границу, у
   человека нужна и нижняя — он может решить посадить всего одно дерево).
5. **Минимальное расстояние между точками ОДНОГО вида посадки** (743-ПП,
   п.3.6.4, Таблица 3.6.2 «групповая посадка» — та же норма и то же число,
   что задаёт шаг сетки автогенератора, см. `pipeline/placement/generator.py`,
   `norm_spacing_for`/`GRID_SPACING_CITATION`). Добавлено 2026-09-24 по прямому
   замечанию пользователя — автогенератор соблюдает эту норму по построению
   (шаг сетки), а ручной ввод мог разместить два дерева буквально в одной
   точке, и до этого момента ни одна проверка это не ловила (густота — это
   агрегатный счётчик на площадь, а не расстояние между конкретной парой
   точек, отступы от коммуникаций — про инфраструктуру, а не про соседние
   растения). Дерево и кустарник в одной точке НЕ считаются конфликтом друг с
   другом — норма именно про растения ОДНОЙ жизненной формы.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

from shapely.geometry.base import BaseGeometry

from pipeline.catalog.dpioos_species_catalog import (
    DpioosSpeciesCatalog,
    LifeForm,
    TerritoryCategory,
    species_conflict_reason,
)
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.catalog.planting_cost import PlantingCostCatalog
from pipeline.constraints.buffer_engine import ConstraintFeature, build_constraint_map
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.otk.offset_compliance import check_offset_compliance
from pipeline.placement.density import PlantingDensityRegistry
from pipeline.placement.generator import GRID_SPACING_CITATION, PlacementPoint, norm_spacing_for


def _ru_plural(count: int, one: str, few: str, many: str) -> str:
    """Родительный/именительный падеж числительного по правилам русского языка
    (1 дерево, 2-4 дерева, 5-20 деревьев, 21 дерево, 22 дерева, ...).

    Найдено пользователем как баг 2026-09-19: было жёстко "деревьев"/
    "кустарников" всегда, из-за чего при густоте=1 текст читался как
    «Предложено 1 деревьев».
    """
    n = abs(count) % 100
    n1 = n % 10
    if 11 <= n <= 14:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def _life_form_count_label(count: int, life_form: LifeForm) -> str:
    if life_form == "tree":
        return _ru_plural(count, "дерево", "дерева", "деревьев")
    return _ru_plural(count, "кустарник", "кустарника", "кустарников")


@dataclass(frozen=True)
class ProposedPlanting:
    id: str
    species_name_ru: str
    life_form: LifeForm
    x: float
    y: float


@dataclass(frozen=True)
class PlacementIssue:
    planting_id: str | None  # None — проблема не привязана к одной точке (например, густота)
    kind: str
    # "unknown_species" | "species_invasive" | "species_not_recommended_for_category" |
    # "species_conflict" | "insufficient_offset" | "outside_site_boundary" |
    # "density_too_high" | "density_too_low" | "spacing_too_close"
    detail: str


@dataclass
class PlacementReviewReport:
    verdict: str  # "ok" | "issues_found"
    issues: list[PlacementIssue] = field(default_factory=list)
    checked_count: int = 0
    explanation: str = ""
    total_estimated_cost_rub: float | None = None
    cost_unknown_count: int = 0  # предложений, для которых вид не найден в каталоге — стоимость не посчитана честно, не додумана


def _estimate_cost(
    proposals: list[ProposedPlanting],
    species_catalog: DpioosSpeciesCatalog,
    cost_catalog: PlantingCostCatalog,
) -> tuple[float, int]:
    """Оценочная стоимость посадок из ЧЕЛОВЕЧЕСКОГО выбора — прямой запрос
    пользователя (2026-09-20): «у нас отсутствует примерный подсчёт стоимости
    посаженных растений при ручном вводе». Та же логика, что и в автогенерации
    (pipeline/run.py: default_cost_for по классифицированной категории
    стоимости вида, см. pipeline/catalog/planting_cost.py), но здесь у КАЖДОЙ
    точки может быть свой вид (не один общий вид на весь участок, как при
    автогенерации) — поэтому стоимость считается и суммируется по каждой
    точке отдельно, а не как unit_cost × count.

    Вид, которого нет в каталоге (уже отдельно диагностируется как
    "unknown_species" в issues), честно исключается из суммы, а не
    додумывается — cost_unknown_count сообщает, сколько точек исключено.
    """
    total = 0.0
    unknown_count = 0
    for proposal in proposals:
        matching = [e for e in species_catalog.lookup(proposal.species_name_ru) if e.life_form == proposal.life_form]
        if not matching:
            unknown_count += 1
            continue
        total += cost_catalog.default_cost_for(matching[0])
    return total, unknown_count


def _check_species_eligibility(
    proposal: ProposedPlanting,
    territory_category: TerritoryCategory,
    species_catalog: DpioosSpeciesCatalog,
    invasive_registry: InvasiveSpeciesRegistry,
) -> list[PlacementIssue]:
    issues: list[PlacementIssue] = []

    if invasive_registry.is_invasive(name_ru=proposal.species_name_ru):
        issues.append(
            PlacementIssue(
                planting_id=proposal.id,
                kind="species_invasive",
                detail=(
                    f"«{proposal.species_name_ru}» входит в перечень инвазивных видов "
                    "(ППМ № 369-ПП, Приложение 1) — высадка запрещена п. 2.4 Приложения 2 "
                    "независимо от категории территории."
                ),
            )
        )

    matching_entries = [e for e in species_catalog.lookup(proposal.species_name_ru) if e.life_form == proposal.life_form]
    if not matching_entries:
        issues.append(
            PlacementIssue(
                planting_id=proposal.id,
                kind="unknown_species",
                detail=(
                    f"«{proposal.species_name_ru}» ({proposal.life_form}) не найден в каталоге "
                    "ДПиООС (data/reference/dpioos_species_assortment.json, 329 видов) — либо "
                    "опечатка в названии, либо вид отсутствует в официальном ассортименте mos.ru "
                    "для внутриквартальных территорий Москвы. Проверить написание вручную."
                ),
            )
        )
        return issues

    if not any(e.is_recommended_for(territory_category) for e in matching_entries):
        category_label = species_catalog.territory_categories.get(territory_category, territory_category)
        issues.append(
            PlacementIssue(
                planting_id=proposal.id,
                kind="species_not_recommended_for_category",
                detail=(
                    f"«{proposal.species_name_ru}» найден в каталоге ДПиООС, но НЕ рекомендован "
                    f"для категории территории «{category_label}» — см. столбец категории в "
                    "исходном файле mos.ru."
                ),
            )
        )

    return issues


def _check_pairwise_conflicts(
    proposals: list[ProposedPlanting],
    species_catalog: DpioosSpeciesCatalog,
) -> list[PlacementIssue]:
    """Сноска [6] ДПиООС — можжевельник (переносчик ржавчинных грибов) рядом с
    яблоней/грушей/айвой. Автогенератор проверяет только пару дерево/кустарник
    (см. pipeline/placement/generator.py) — здесь человек мог предложить любое
    число видов, проверяем ВСЕ различающиеся пары названий."""
    issues: list[PlacementIssue] = []
    unique_names = sorted({p.species_name_ru for p in proposals})
    entries_by_name = {name: species_catalog.lookup(name) for name in unique_names}

    for name_a, name_b in combinations(unique_names, 2):
        for entry_a in entries_by_name[name_a]:
            for entry_b in entries_by_name[name_b]:
                reason = species_conflict_reason(entry_a, entry_b)
                if reason is None:
                    continue
                affected_ids = [p.id for p in proposals if p.species_name_ru in (name_a, name_b)]
                for planting_id in affected_ids:
                    issues.append(
                        PlacementIssue(
                            planting_id=planting_id,
                            kind="species_conflict",
                            detail=(
                                f"Конфликт между «{name_a}» и «{name_b}» на одном участке: {reason} "
                                "(сноска [6], каталог ДПиООС)."
                            ),
                        )
                    )
                break  # одной найденной причины на пару видов достаточно, не дублируем по всем entry

    return issues


def _check_density(
    proposals: list[ProposedPlanting],
    life_form: LifeForm,
    territory_category: TerritoryCategory,
    allowed_zone_area_m2: float,
    density_registry: PlantingDensityRegistry,
) -> list[PlacementIssue]:
    proposed_count = sum(1 for p in proposals if p.life_form == life_form)
    if proposed_count == 0:
        return []

    min_count = density_registry.min_count_for_area(territory_category, life_form, allowed_zone_area_m2)
    max_count = density_registry.max_count_for_area(territory_category, life_form, allowed_zone_area_m2)
    life_form_label = _life_form_count_label(proposed_count, life_form)

    if proposed_count > max_count:
        return [
            PlacementIssue(
                planting_id=None,
                kind="density_too_high",
                detail=(
                    f"Предложено {proposed_count} {life_form_label} на допустимой площади "
                    f"{allowed_zone_area_m2:.1f} кв. м — это выше верхней границы нормы плотности "
                    f"623-ПП/МГСН 1.02-02, Таблица В.1 ({max_count} шт. для этой категории "
                    "территории и площади). Нужно либо уменьшить число посадок, либо "
                    "пересмотреть площадь размещения."
                ),
            )
        ]
    if proposed_count < min_count:
        return [
            PlacementIssue(
                planting_id=None,
                kind="density_too_low",
                detail=(
                    f"Предложено {proposed_count} {life_form_label} на допустимой площади "
                    f"{allowed_zone_area_m2:.1f} кв. м — это ниже нижней границы нормы плотности "
                    f"623-ПП/МГСН 1.02-02, Таблица В.1 ({min_count} шт. для этой категории "
                    "территории и площади). Это не запрет (норма задаёт ориентир, не жёсткий "
                    "минимум для отдельного участка), но стоит явно решить, оправдано ли "
                    "занижение."
                ),
            )
        ]
    return []


def _check_minimum_spacing(
    proposals: list[ProposedPlanting],
    life_form: LifeForm,
    planting_density: dict,
) -> list[PlacementIssue]:
    """743-ПП, п.3.6.4, Таблица 3.6.2 «групповая посадка» — та же норма, что
    задаёт шаг сетки автогенератора (см. GRID_SPACING_CITATION/norm_spacing_for
    в pipeline/placement/generator.py). Автогенератор соблюдает её по
    построению (сетка); здесь — независимая геометрическая проверка для
    человеческого выбора, где расстояние между двумя точками ОДНОГО вида
    посадки может оказаться каким угодно, вплоть до нуля. Разные жизненные
    формы (дерево vs кустарник) друг на друга не влияют — норма про
    расстояние ВНУТРИ одной группы одного вида посадки."""
    kind_proposals = [p for p in proposals if p.life_form == life_form]
    if len(kind_proposals) < 2:
        return []

    min_spacing_m = norm_spacing_for(life_form, planting_density)
    issues: list[PlacementIssue] = []
    for a, b in combinations(kind_proposals, 2):
        distance_m = math.dist((a.x, a.y), (b.x, b.y))
        if distance_m >= min_spacing_m:
            continue
        detail = (
            f"Расстояние между «{a.species_name_ru}» ({a.id}) и «{b.species_name_ru}» ({b.id}) — "
            f"{distance_m:.2f} м, это меньше минимального шага {min_spacing_m:.1f} м "
            f"({GRID_SPACING_CITATION}). Кроны/корневые системы физически пересекутся."
        )
        issues.append(PlacementIssue(planting_id=a.id, kind="spacing_too_close", detail=detail))
        issues.append(PlacementIssue(planting_id=b.id, kind="spacing_too_close", detail=detail))
    return issues


def review_human_placements(
    site_boundary: BaseGeometry,
    features: list[ConstraintFeature],
    territory_category: TerritoryCategory,
    proposals: list[ProposedPlanting],
    species_catalog: DpioosSpeciesCatalog | None = None,
    invasive_registry: InvasiveSpeciesRegistry | None = None,
    offset_registry: OffsetRegistry | None = None,
    density_registry: PlantingDensityRegistry | None = None,
    cost_catalog: PlantingCostCatalog | None = None,
) -> PlacementReviewReport:
    """Независимая проверка предложенного человеком размещения — не переиспользует
    никакие решения автогенератора (`pipeline/placement/generator.py`), только
    сырые входные данные участка (site_boundary/features) и верифицированные
    справочники норм. Честно диагностирует нарушения без автоисправления."""
    species_catalog = species_catalog or DpioosSpeciesCatalog()
    invasive_registry = invasive_registry or InvasiveSpeciesRegistry()
    offset_registry = offset_registry or OffsetRegistry()
    density_registry = density_registry or PlantingDensityRegistry()
    cost_catalog = cost_catalog or PlantingCostCatalog()

    issues: list[PlacementIssue] = []

    for proposal in proposals:
        issues.extend(
            _check_species_eligibility(proposal, territory_category, species_catalog, invasive_registry)
        )
    issues.extend(_check_pairwise_conflicts(proposals, species_catalog))

    for life_form in ("tree", "shrub"):
        kind_proposals = [p for p in proposals if p.life_form == life_form]
        if not kind_proposals:
            continue

        placement_points = [
            PlacementPoint(
                id=p.id,
                planting_kind=life_form,
                x=p.x,
                y=p.y,
                status="placed",
                species_name_ru=p.species_name_ru,
                species_citation=None,
                grid_spacing_m=0.0,
                grid_spacing_citation="",
            )
            for p in kind_proposals
        ]
        offset_check = check_offset_compliance(
            site_boundary=site_boundary,
            features=features,
            registry=offset_registry,
            planting_kind=life_form,
            points=placement_points,
        )
        for violation in offset_check.violations:
            issues.append(
                PlacementIssue(planting_id=violation.point_id, kind=violation.kind, detail=violation.detail)
            )

        constraint_result = build_constraint_map(site_boundary, features, life_form, offset_registry)
        issues.extend(
            _check_density(
                proposals, life_form, territory_category, constraint_result.allowed_zone.area, density_registry
            )
        )
        issues.extend(_check_minimum_spacing(proposals, life_form, offset_registry.planting_density))

    verdict = "ok" if not issues else "issues_found"
    explanation = (
        f"Независимая проверка {len(proposals)} предложенных человеком посадок: все виды "
        "допустимы, отступы и густота соответствуют нормам."
        if verdict == "ok"
        else f"Найдено замечаний: {len(issues)} — см. issues. Сервис не вносит корректировок в "
        "предлагаемую пользователем конфигурацию. Сервис осуществляет автоматизированный "
        "анализ допустимости размещения объектов на основании нормативных правовых актов. "
        "Данные сведения носят рекомендательный характер, решение о допустимости остаётся "
        "за пользователем."
    )
    total_cost, cost_unknown_count = _estimate_cost(proposals, species_catalog, cost_catalog)
    return PlacementReviewReport(
        verdict=verdict,
        issues=issues,
        checked_count=len(proposals),
        explanation=explanation,
        total_estimated_cost_rub=total_cost if proposals else None,
        cost_unknown_count=cost_unknown_count,
    )
