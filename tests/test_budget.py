"""Тесты бюджетного ограничения густоты посадки (pipeline/placement/budget.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.placement.budget import compute_budget_constrained_spacing


def test_budget_widens_spacing_when_norm_would_be_unrealistically_dense():
    """Реальный сценарий 2026-09-16: 0,3 м по норме на участке 490000 м² (49 га)
    даёт ~5.4 млн потенциальных узлов сетки — бюджет обязан расширить шаг."""
    result = compute_budget_constrained_spacing(
        allowed_area_m2=490_000,
        norm_spacing_m=0.3,
        unit_cost_rub=700,
        already_spent_rub=432 * 25_000,  # 432 дерева по 25000 руб (условно крупномер)
        target_budget_rub=490_000 / 10_000 * 17_500_000,  # 49 га * 17.5 млн/га (середина 10-25)
    )
    assert result.spacing_m > 0.3
    assert "увеличен" in result.explanation


def test_budget_does_not_narrow_spacing_below_norm():
    """Если бюджета много (например, маленький участок), шаг не должен стать
    ГУЩЕ физической нормы — норма остаётся жёстким верхним пределом плотности."""
    result = compute_budget_constrained_spacing(
        allowed_area_m2=100,
        norm_spacing_m=0.3,
        unit_cost_rub=700,
        already_spent_rub=0,
        target_budget_rub=10_000_000,  # огромный бюджет на крошечный участок
    )
    assert result.spacing_m == 0.3
    assert "не ограничивает" in result.explanation


def test_exhausted_budget_gives_honest_sparse_spacing_not_zero_or_crash():
    result = compute_budget_constrained_spacing(
        allowed_area_m2=490_000,
        norm_spacing_m=0.3,
        unit_cost_rub=700,
        already_spent_rub=30_000_000,  # уже потрачено больше целевого бюджета
        target_budget_rub=17_500_000,
    )
    assert result.spacing_m == 490_000**0.5
    assert "исчерпан" in result.explanation


def test_zero_area_does_not_divide_by_zero():
    result = compute_budget_constrained_spacing(
        allowed_area_m2=0,
        norm_spacing_m=0.3,
        unit_cost_rub=700,
        already_spent_rub=0,
        target_budget_rub=17_500_000,
    )
    assert result.spacing_m == 0.3


if __name__ == "__main__":
    test_budget_widens_spacing_when_norm_would_be_unrealistically_dense()
    test_budget_does_not_narrow_spacing_below_norm()
    test_exhausted_budget_gives_honest_sparse_spacing_not_zero_or_crash()
    test_zero_area_does_not_divide_by_zero()
    print("OK: budget-constrained spacing tests passed")
