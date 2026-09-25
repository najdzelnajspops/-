"""Бюджетное ограничение густоты посадки (2026-09-16, по запросу пользователя).

НЕ норма — организаторы хакатона устно (не документом) сообщили ориентир
стоимости озеленения по Москве: 10-25 млн руб/га (в районах реновации — до
50 млн, не используется по умолчанию). Реальная цена «плавает» по питомнику,
поэтому все стоимости — `data/reference/planting_cost_estimates.yaml`,
редактируемые человеком (см. docstring файла).

ПРОБЛЕМА, КОТОРУЮ ЭТО РЕШАЕТ (docs/OPEN_QUESTIONS.md, 2026-09-16): шаг сетки
кустарника, взятый из чисто физической нормы «групповая посадка» (743-ПП,
0,3 м), даёт нереалистичное число посадок на большом участке (292932 куста на
49 га) — норма осмысленна ДЛЯ ГРУППЫ/ИЗГОРОДИ, но не как шаг сетки по всей
территории. Бюджет даёт вторую, независимую точку опоры для density помимо
чистой геометрии.

ПРИНЦИП: дерево размещается по норме (743-ПП, физически обоснованный шаг,
уже даёт правдоподобное число — 432 на реальном объекте). Оставшийся после
деревьев бюджет распределяется на кустарник — шаг сетки кустарника
пересчитывается так, чтобы уложиться в остаток, НО не гуще, чем позволяет
физическая норма (бюджет может сделать посадку РЕЖЕ нормы, но не ГУЩЕ —
942-ПП/743-ПП остаётся жёстким физическим ограничением сверху по плотности).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetConstrainedSpacing:
    spacing_m: float
    explanation: str


def compute_budget_constrained_spacing(
    allowed_area_m2: float,
    norm_spacing_m: float,
    unit_cost_rub: float,
    already_spent_rub: float,
    target_budget_rub: float,
) -> BudgetConstrainedSpacing:
    """Возвращает итоговый шаг сетки для вида посадки, чей бюджет считается
    ПОСЛЕ уже размещённых (обычно деревьев). Шаг — максимум из физически-
    нормативного (нельзя гуще нормы) и бюджетного (нельзя дороже остатка
    бюджета) значений.
    """
    remaining_budget_rub = target_budget_rub - already_spent_rub

    if allowed_area_m2 <= 0:
        return BudgetConstrainedSpacing(
            spacing_m=norm_spacing_m,
            explanation="Допустимая зона нулевая — шаг сетки не имеет значения (посадок не будет).",
        )

    if remaining_budget_rub <= 0 or unit_cost_rub <= 0:
        # Бюджет уже исчерпан другими посадками (обычно деревьями) — размещаем
        # по минимуму, не по нулю (честный сигнал "бюджета не осталось", а не
        # молчаливое исключение вида целиком).
        spacing_m = allowed_area_m2**0.5
        explanation = (
            f"Целевой бюджет ({target_budget_rub:,.0f} руб.) уже исчерпан другими посадками "
            f"({already_spent_rub:,.0f} руб.) — шаг сетки максимально разрежен (одна точка на "
            "зону), а не проигнорирован."
        ).replace(",", " ")
        return BudgetConstrainedSpacing(spacing_m=spacing_m, explanation=explanation)

    desired_count = max(remaining_budget_rub / unit_cost_rub, 1.0)
    budget_spacing_m = (allowed_area_m2 / desired_count) ** 0.5

    if budget_spacing_m >= norm_spacing_m:
        final_spacing = budget_spacing_m
        explanation = (
            f"Шаг сетки увеличен с нормативных {norm_spacing_m:.2f} м до {final_spacing:.2f} м, "
            f"чтобы уложиться в оставшийся бюджет ({remaining_budget_rub:,.0f} руб. из целевых "
            f"{target_budget_rub:,.0f} руб/участок при цене {unit_cost_rub:,.0f} руб/шт.) — "
            "физическая норма 743-ПП допускала бы более густую посадку, но это дало бы "
            "нереалистичное число экземпляров (см. docs/OPEN_QUESTIONS.md, 2026-09-16)."
        ).replace(",", " ")
    else:
        final_spacing = norm_spacing_m
        explanation = (
            f"Бюджета достаточно для шага гуще нормы (~{budget_spacing_m:.2f} м) — "
            f"используется физически-нормативный шаг {norm_spacing_m:.2f} м как жёсткая "
            "верхняя граница плотности (743-ПП), бюджет в этом случае не ограничивает."
        )

    return BudgetConstrainedSpacing(spacing_m=final_spacing, explanation=explanation)
