"""Placement Generator Agent — MVP, детерминированные правила (Service A).

См. docs/AGENTS_PLAN.md, агент №5. Берёт уже посчитанную допустимую зону
(ConstraintMapResult — отдельно для tree и shrub, pipeline/constraints/buffer_engine.py)
и категорию территории участка, генерирует конкретные точки посадки с указанием
вида растения и обоснования.

Категория территории — ВХОДНОЙ ПАРАМЕТР прогона, а не результат анализа геометрии
DXF (см. ТЗ п.7 — организаторы предоставляют «наборы параметров для тестовых
участков»; сам DXF не содержит надёжного признака «это школьная территория» или
«это магистраль»).

MVP-подход, сознательно простой (см. docs/ARCHITECTURE.md §3.1c):
- Точки-кандидаты — регулярная сетка с шагом из УЖЕ ВЕРИФИЦИРОВАННОЙ нормы
  743-ПП п.3.6.4/Таблица 3.6.2 («групповая посадка») — data/reference/offset_norms.yaml,
  planting_density_recommendations. Не придуманное число.
- Сетка выравнивается по (minx, miny) bounding box допустимой зоны — фиксированная
  точка отсчёта, для повторяемости результата между прогонами (ARCHITECTURE.md §3.1).
- Точка принимается, если строго внутри allowed_zone (shapely.contains).
- Вид растения — первый по алфавиту вид из перечня, рекомендованного для данной
  категории территории (ДПиООС, data/reference/dpioos_species_assortment.json)
  для нужной жизненной формы, прошедший проверку на инвазивность (369-ПП). Это
  НЕ горticultурно-оптимальный выбор, а простое воспроизводимое правило-заглушка.
- **Фитосанитарная совместимость дерева и кустарника на одном участке** (2026-09-16,
  по инициативе пользователя) — единственное правило, для которого есть
  верифицированный источник: сноска [6] каталога ДПиООС (можжевельник —
  переносчик ржавчинных грибов, конфликтует с яблоней/грушей/айвой). Вид для
  ВТОРОЙ обрабатываемой жизненной формы подбирается с исключением кандидатов,
  конфликтующих с уже выбранным видом первой формы (см. `avoid_conflict_with`
  ниже, `pipeline.catalog.dpioos_species_catalog.species_conflict_reason`).
  Дистанция «вблизи» самой сноской не задана числом — намеренно НЕ придумывается
  новая, а берётся тот же уже верифицированный шаг сетки (см. `norm_spacing_for`) —
  честно задокументированная инженерная интерпретация, не отдельная норма.
- Сознательно НЕ учитывается (см. docs/OPEN_QUESTIONS.md, ARCHITECTURE.md §3.1c):
  более широкая совместимость/аллелопатия (кроме правила выше) и требования к
  освещённости (теневая сторона зданий) — для обоих нет верифицированного
  источника/данных (ни ориентации/этажности зданий из DXF, ни светолюбия вида
  в каталоге ДПиООС).
- Если для категории/жизненной формы нет ни одного рекомендованного и
  не-инвазивного вида — точка помечается `no_recommended_species`, а не получает
  произвольный вид. Это тоже обязательный по ТЗ случай «отклонено» — фиксируется
  с причиной, а не пропускается молча.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, sqrt
from typing import Literal

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from pipeline.catalog.dpioos_species_catalog import (
    DpioosSpeciesCatalog,
    DpioosSpeciesEntry,
    LifeForm,
    TerritoryCategory,
    species_conflict_reason,
)
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.constraints.offset_registry import PlantingKind

PlacementStatus = Literal["placed", "no_recommended_species"]


@dataclass(frozen=True)
class PlacementPoint:
    id: str
    planting_kind: PlantingKind
    x: float
    y: float
    status: PlacementStatus
    species_name_ru: str | None
    species_citation: str | None
    grid_spacing_m: float
    grid_spacing_citation: str


@dataclass
class PlacementResult:
    planting_kind: PlantingKind
    territory_category: TerritoryCategory
    points: list[PlacementPoint] = field(default_factory=list)
    chosen_species_entry: DpioosSpeciesEntry | None = None
    # Многовидовой выбор (2026-09-24, чек-боксы видов на вкладке автогенерации
    # веб-UI, см. docstring generate_placement про selected_species_names) —
    # ВСЕ виды, реально распределённые по точкам этой жизненной формы, в
    # алфавитном порядке. При однвидовом режиме (по умолчанию, без выбора
    # пользователя) содержит либо [chosen_species_entry], либо [] (если вид не
    # найден) — то есть chosen_species_entry всегда согласован с первым
    # элементом этого списка, не отдельная, потенциально расходящаяся правда.
    chosen_species_entries: list[DpioosSpeciesEntry] = field(default_factory=list)
    species_conflict_note: str | None = None
    eligible_alternatives: list[str] = field(default_factory=list)
    density_note: str | None = None  # пояснение густоты — обычно от density.py (623-ПП) или budget.py (не используется по умолчанию, см. pipeline/run.py)
    estimated_cost_rub: float | None = None  # заполняется снаружи (pipeline/run.py), см. pipeline/catalog/planting_cost.py

    def placed_points(self) -> list[PlacementPoint]:
        return [p for p in self.points if p.status == "placed"]

    def rejected_points(self) -> list[PlacementPoint]:
        return [p for p in self.points if p.status != "placed"]


GRID_SPACING_CITATION = "ППМ № 743-ПП, п. 3.6.4, Таблица 3.6.2 — «групповая посадка»"


def norm_spacing_for(planting_kind: PlantingKind, registry_density: dict) -> float:
    """Берёт середину диапазона «групповая посадка» из planting_density_recommendations
    (offset_norms.yaml) — сам диапазон verified, конкретная середина — инженерный
    выбор внутри проверенного диапазона (тот же честный паттерн, что и в
    pipeline/constraints/existing_vegetation.py: диапазон из нормы, точка внутри
    диапазона — решение разработчика, не нужно домысливать её как отдельную норму)."""
    if planting_kind == "tree":
        lo, hi = registry_density["group_planting_trees_m"]
        return (lo + hi) / 2
    if planting_kind == "shrub":
        value = registry_density["group_planting_shrubs_m"]
        return float(value) if not isinstance(value, list) else (value[0] + value[1]) / 2
    raise ValueError(f"Неизвестный тип посадки: {planting_kind!r}")


def _grid_candidates(allowed_zone: BaseGeometry, spacing_m: float) -> list[tuple[float, float]]:
    if allowed_zone.is_empty or spacing_m <= 0:
        return []
    minx, miny, maxx, maxy = allowed_zone.bounds
    candidates: list[tuple[float, float]] = []
    row = 0
    while True:
        y = miny + row * spacing_m
        if y > maxy:
            break
        col = 0
        while True:
            x = minx + col * spacing_m
            if x > maxx:
                break
            candidates.append((x, y))
            col += 1
        row += 1
    return candidates


# Инженерный выбор (НЕ норма — источник с точным числом «сколько экземпляров
# в одной группе» не найден ни в одном из проверенных документов, 2026-09-16):
# небольшая, компактная группа кустарника. Подобрано так, чтобы группа была
# заметно меньше "сплошного покрытия", но не единичным кустом — соответствует
# формулировке СП82.13330.2016 п.9.30 "небольшие группы", без домысливания
# точного числа как проверенной величины.
DEFAULT_SHRUB_GROUP_SIZE = 7


@dataclass(frozen=True)
class ClusterConfig:
    group_size: int
    group_spacing_m: float
    intra_group_spacing_m: float


def _cluster_candidates(allowed_zone: BaseGeometry, config: ClusterConfig) -> list[tuple[float, float]]:
    """Точки-кандидаты небольшими компактными группами, а не сплошной сеткой —
    см. СП82.13330.2016 п.9.30: придомовая территория состоит из газона с
    посадками небольших групп кустарника, а не сплошного покрытия кустами.
    Центры групп расставлены редкой сеткой (group_spacing_m — подобран снаружи
    так, чтобы итоговое число точек соответствовало целевой густоте 623-ПП,
    Таблица В.1, см. pipeline/placement/density.py). Внутри группы точки идут
    с шагом intra_group_spacing_m — здесь наконец корректно применяется норма
    «групповая посадка» (743-ПП, Таблица 3.6.2), рассчитанная на расстояние
    ВНУТРИ одной группы, а не на шаг сетки по всей территории (баг, найденный
    2026-09-16, см. docs/SESSION_LOG.md)."""
    if allowed_zone.is_empty or config.group_spacing_m <= 0:
        return []
    side = ceil(sqrt(config.group_size))
    candidates: list[tuple[float, float]] = []
    for cx, cy in _grid_candidates(allowed_zone, config.group_spacing_m):
        placed_in_group = 0
        for row in range(side):
            if placed_in_group >= config.group_size:
                break
            for col in range(side):
                if placed_in_group >= config.group_size:
                    break
                x = cx + col * config.intra_group_spacing_m
                y = cy + row * config.intra_group_spacing_m
                candidates.append((x, y))
                placed_in_group += 1
    return candidates


TIE_BREAK_EXPLANATION = (
    "Все перечисленные альтернативы равно допустимы по каталогу ДПиООС для этой категории "
    "территории (не инвазивны, не конфликтуют по сноске [6]) — источник не даёт критерия, "
    "чтобы предпочесть один вид другому среди них. Выбор сделан по алфавиту как единственный "
    "детерминированный (воспроизводимый между прогонами) тай-брейк, а не как содержательное "
    "предпочтение одного вида перед другим."
)


def _conflict_reason_against_any(
    entry: DpioosSpeciesEntry, others: list[DpioosSpeciesEntry]
) -> tuple[str, str] | None:
    """Первая найденная причина конфликта entry с любым из others — вместе с
    именем того, с кем именно конфликт (для честного сообщения пользователю,
    не просто "с чем-то из уже выбранного")."""
    for other in others:
        reason = species_conflict_reason(entry, other)
        if reason is not None:
            return reason, other.name_ru
    return None


def pick_species(
    territory_category: TerritoryCategory,
    life_form: LifeForm,
    species_catalog: DpioosSpeciesCatalog,
    invasive_registry: InvasiveSpeciesRegistry,
    avoid_conflict_with: list[DpioosSpeciesEntry] | None = None,
) -> tuple[DpioosSpeciesEntry | None, str | None, list[str]]:
    """Возвращает (выбранный_вид, заметка_о_конфликте, альтернативы_по_алфавиту).

    заметка_о_конфликте — не None только если по пути пришлось пропустить хотя бы
    одного кандидата из-за конфликта с avoid_conflict_with (сноска [6]).
    avoid_conflict_with — СПИСОК (2026-09-24, был одиночным DpioosSpeciesEntry
    до многовидового режима автогенерации, см. pick_species_multi) — кандидат
    исключается, если конфликтует хотя бы с ОДНИМ из уже выбранных видов другой
    жизненной формы, не только с единственным, как раньше.

    альтернативы — имена ВСЕХ ОСТАЛЬНЫХ кандидатов, прошедших те же фильтры
    (рекомендован для категории+формы, не инвазивен, не конфликтует), кроме
    выбранного. Нужны для честного ответа на вопрос «почему этот вид, а не
    другой равно допустимый» (см. TIE_BREAK_EXPLANATION) — не скрывать выбор
    за одной строкой цитаты, а явно показать, что был выбор и на основании чего
    (точнее — что содержательного основания предпочесть один вид другому нет,
    кроме детерминированного тай-брейка)."""
    candidates = sorted(
        species_catalog.recommended_for(territory_category, life_form), key=lambda e: e.name_ru
    )
    eligible: list[DpioosSpeciesEntry] = []
    skipped: list[tuple[DpioosSpeciesEntry, str, str]] = []
    for entry in candidates:
        if invasive_registry.is_invasive(name_ru=entry.name_ru):
            continue
        if avoid_conflict_with:
            conflict = _conflict_reason_against_any(entry, avoid_conflict_with)
            if conflict is not None:
                reason, other_name = conflict
                skipped.append((entry, reason, other_name))
                continue
        eligible.append(entry)

    if not eligible:
        return None, None, []

    chosen = eligible[0]
    alternatives = [e.name_ru for e in eligible[1:]]

    conflict_note = None
    if skipped:
        skipped_names = ", ".join(f"«{e.name_ru}»" for e, _, _ in skipped)
        conflict_note = (
            f"Исключены из выбора как конфликтующие с уже выбранным «{skipped[0][2]}»: "
            f"{skipped_names} ({skipped[0][1]})"
        )
    return chosen, conflict_note, alternatives


def pick_species_multi(
    territory_category: TerritoryCategory,
    life_form: LifeForm,
    species_catalog: DpioosSpeciesCatalog,
    invasive_registry: InvasiveSpeciesRegistry,
    selected_names: list[str],
    avoid_conflict_with: list[DpioosSpeciesEntry] | None = None,
) -> tuple[list[DpioosSpeciesEntry], str | None]:
    """Многовидовой выбор — прямой запрос пользователя (2026-09-24): чек-боксы
    видов на вкладке автогенерации веб-UI («список растений с чек-боксами,
    чтобы пользователь мог сначала задать перечень растений, которые он хочет
    разместить»), см. docs/OPEN_QUESTIONS.md. В отличие от pick_species() (один
    вид, первый по алфавиту среди ВСЕХ рекомендованных), здесь пользователь уже
    сам сузил список — сервис лишь честно фильтрует его теми же двумя
    проверками, что и pick_species (рекомендован+не инвазивен уже гарантированы
    тем, что selected_names пришли из /api/species, но перепроверяются здесь
    же — не доверяем входу молча), плюс фитосанитарный конфликт с уже выбранными
    видами ДРУГОЙ жизненной формы. Оставшиеся виды возвращаются ВСЕ — их
    распределение по точкам (round-robin по этому же алфавитному порядку) —
    в generate_placement(), не здесь (эта функция ничего не знает про точки)."""
    selected_norm = {n.strip().lower() for n in selected_names}
    candidates = sorted(
        (e for e in species_catalog.recommended_for(territory_category, life_form) if e.name_ru.strip().lower() in selected_norm),
        key=lambda e: e.name_ru,
    )

    eligible: list[DpioosSpeciesEntry] = []
    excluded: list[str] = []
    for entry in candidates:
        if invasive_registry.is_invasive(name_ru=entry.name_ru):
            excluded.append(f"«{entry.name_ru}» — инвазивный вид (369-ПП)")
            continue
        if avoid_conflict_with:
            conflict = _conflict_reason_against_any(entry, avoid_conflict_with)
            if conflict is not None:
                reason, other_name = conflict
                excluded.append(f"«{entry.name_ru}» — конфликт с «{other_name}» ({reason})")
                continue
        eligible.append(entry)

    note = "Исключены из выбранного набора: " + "; ".join(excluded) if excluded else None
    return eligible, note


def generate_placement(
    constraint_result: ConstraintMapResult,
    territory_category: TerritoryCategory,
    life_form: LifeForm,
    species_catalog: DpioosSpeciesCatalog,
    invasive_registry: InvasiveSpeciesRegistry,
    planting_density: dict,
    avoid_conflict_with: list[DpioosSpeciesEntry] | None = None,
    spacing_override_m: float | None = None,
    density_note: str | None = None,
    cluster_config: ClusterConfig | None = None,
    selected_species_names: list[str] | None = None,
) -> PlacementResult:
    """spacing_override_m/density_note (2026-09-16): если задан spacing_override_m,
    используется ВМЕСТО чисто нормативного шага (см. pipeline/placement/budget.py —
    бюджетное ограничение густоты, не используется по умолчанию, см. pipeline/run.py).
    Игнорируется, если задан cluster_config.

    cluster_config (2026-09-16, приоритетный механизм густоты кустарника):
    если задан, точки-кандидаты генерируются небольшими компактными группами
    (см. `_cluster_candidates`), а не сплошной сеткой — отражает реальную
    практику (СП82.13330.2016 п.9.30) и настоящую норму плотности (623-ПП,
    Таблица В.1, см. pipeline/placement/density.py), а не чисто геометрический
    шаг. `spacing_m`, записанный на точку, в этом случае — расстояние ВНУТРИ
    группы (`cluster_config.intra_group_spacing_m`), не шаг между группами.

    selected_species_names (2026-09-24, чек-боксы видов на вкладке автогенерации
    веб-UI, см. pick_species_multi): если задан — НЕСКОЛЬКО видов распределяются
    по точкам-кандидатам round-robin (по кругу) в алфавитном порядке отфильтрованного
    списка (детерминированно, без seed — тот же принцип воспроизводимости, что и
    везде в проекте, см. docs/ARCHITECTURE.md §3.1). Если после фильтров (369-ПП,
    конфликт с другой формой) не осталось ни одного вида — точки честно получают
    `no_recommended_species`, как и в однвидовом режиме, а не ошибку/исключение.
    Если None (по умолчанию) — поведение НЕ меняется: один вид, первый по
    алфавиту (pick_species)."""
    planting_kind: PlantingKind = constraint_result.planting_kind
    allowed = constraint_result.allowed_zone

    if cluster_config is not None:
        spacing_m = cluster_config.intra_group_spacing_m
        candidates = _cluster_candidates(allowed, cluster_config)
    else:
        spacing_m = spacing_override_m if spacing_override_m is not None else norm_spacing_for(planting_kind, planting_density)
        candidates = _grid_candidates(allowed, spacing_m)

    if selected_species_names is not None:
        eligible_entries, conflict_note = pick_species_multi(
            territory_category, life_form, species_catalog, invasive_registry, selected_species_names, avoid_conflict_with
        )
        all_recommended_names = {e.name_ru for e in species_catalog.recommended_for(territory_category, life_form)}
        eligible_names = {e.name_ru for e in eligible_entries}
        alternatives = sorted(all_recommended_names - eligible_names)
    else:
        chosen, conflict_note, alternatives = pick_species(
            territory_category, life_form, species_catalog, invasive_registry, avoid_conflict_with
        )
        eligible_entries = [chosen] if chosen is not None else []

    citations_by_name = {e.name_ru: species_catalog.citation_for(e, territory_category) for e in eligible_entries}

    points: list[PlacementPoint] = []
    idx = 0
    placed_idx = 0
    for x, y in candidates:
        pt = Point(x, y)
        if not allowed.contains(pt):
            continue
        idx += 1
        point_id = f"{planting_kind}_{idx:04d}"
        if not eligible_entries:
            points.append(
                PlacementPoint(
                    id=point_id,
                    planting_kind=planting_kind,
                    x=x,
                    y=y,
                    status="no_recommended_species",
                    species_name_ru=None,
                    species_citation=None,
                    grid_spacing_m=spacing_m,
                    grid_spacing_citation=GRID_SPACING_CITATION,
                )
            )
            continue
        # round-robin по eligible_entries (список из 1 элемента в однвидовом
        # режиме ведёт себя как раньше — тот же вид на каждую точку).
        point_species = eligible_entries[placed_idx % len(eligible_entries)]
        placed_idx += 1
        points.append(
            PlacementPoint(
                id=point_id,
                planting_kind=planting_kind,
                x=x,
                y=y,
                status="placed",
                species_name_ru=point_species.name_ru,
                species_citation=citations_by_name[point_species.name_ru],
                grid_spacing_m=spacing_m,
                grid_spacing_citation=GRID_SPACING_CITATION,
            )
        )

    return PlacementResult(
        planting_kind=planting_kind,
        territory_category=territory_category,
        points=points,
        chosen_species_entry=eligible_entries[0] if eligible_entries else None,
        chosen_species_entries=eligible_entries,
        species_conflict_note=conflict_note,
        eligible_alternatives=alternatives,
        density_note=density_note,
    )
