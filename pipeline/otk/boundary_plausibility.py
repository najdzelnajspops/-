"""Агент ОТК v1 — независимая проверка правдоподобия границы участка.

См. docs/AGENTS_PLAN.md, агент №6, проверка 6.1: «независимый пересчёт
расстояний напрямую по исходной геометрии, не через внутренние переменные
генератора». Часть 6.1 (bounding-box-эвристика для границы участка) —
остальные проверки (6.1 продолжение, 6.2, 6.3, 6.4) реализованы отдельными
модулями, см. docs/CHECKLIST.md; 6.5 не применяется (см. docs/OPEN_QUESTIONS.md).

Найдена и обоснована реальным случаем 2026-09-16 (см. docs/DATA_STRUCTURE.md
§11.3): баг выбора границы в Ingest (приоритет ранга имени слоя перебивал
достоверность геометрии) давал `site_boundary_status = "found"` с площадью
46,9 м² вместо настоящих 494 315 м² — эта проверка поймала бы такой случай
автоматически, если бы уже существовала на тот момент.

ПРИНЦИП НЕЗАВИСИМОСТИ (обязателен для агента ОТК, см. AGENTS_PLAN.md): эта
проверка НЕ использует ConstraintMapResult/PlacementResult (внутренние
переменные Constraint Engine/Placement Generator) — только сырые
`IngestResult.site_boundary` и `IngestResult.features` (НЕ буферизованную
геометрию объектов), пересчитывает с нуля независимо от того, что решил
основной пайплайн.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pipeline.constraints.buffer_engine import ConstraintFeature

# Если площадь границы участка меньше этой доли от площади bounding box всех
# объектов-коммуникаций на участке — подозрительно мало для реального участка.
# Порог подобран не формальным расчётом, а по наблюдению на реальном случае
# (см. докстринг модуля): корректная граница «Олимпийской деревни» — 494 315 м²,
# дефектная — 46,9 м² (в ~10000 раз меньше охвата коммуникаций) — разница на
# порядки, порог 1% даёт большой запас, не подгонка под конкретное число.
_MIN_BOUNDARY_TO_FEATURES_BBOX_RATIO = 0.01

Verdict = Literal["ok", "implausible", "not_checked"]


@dataclass(frozen=True)
class BoundaryPlausibilityCheck:
    verdict: Verdict
    boundary_area_m2: float | None
    features_bbox_area_m2: float | None
    ratio: float | None
    explanation: str


def check_boundary_plausibility(
    site_boundary: BaseGeometry | None,
    features: list[ConstraintFeature],
) -> BoundaryPlausibilityCheck:
    """Независимая (не полагающаяся на site_boundary_status из Ingest) оценка:
    правдоподобна ли площадь границы относительно охвата объектов-коммуникаций
    на том же участке. Это ЭВРИСТИКА для привлечения внимания оператора, а не
    формальная норма — вердикт `implausible` не блокирует пайплайн сам по себе,
    но обязан быть виден в CLI и в JSON-отчёте (см. pipeline/run.py)."""
    if site_boundary is None or site_boundary.is_empty:
        return BoundaryPlausibilityCheck(
            verdict="not_checked",
            boundary_area_m2=None,
            features_bbox_area_m2=None,
            ratio=None,
            explanation="Граница участка не определена — проверка правдоподобия неприменима.",
        )
    if not features:
        return BoundaryPlausibilityCheck(
            verdict="not_checked",
            boundary_area_m2=site_boundary.area,
            features_bbox_area_m2=None,
            ratio=None,
            explanation="Нет объектов-коммуникаций для сравнения — проверка правдоподобия неприменима.",
        )

    minx, miny, maxx, maxy = unary_union([f.geometry for f in features]).bounds
    features_bbox_area = (maxx - minx) * (maxy - miny)
    boundary_area = site_boundary.area
    ratio = boundary_area / features_bbox_area if features_bbox_area > 0 else None

    # "кв. м", не "м²" — найдено 2026-09-24 на реальном объекте («Грузинская М
    # ул»): символ «²» (U+00B2) отсутствует в cp1251, CLI-вывод падал с
    # UnicodeEncodeError при перенаправлении в файл (`> log.txt`) на Windows,
    # где stdout по умолчанию не UTF-8.
    if ratio is not None and ratio < _MIN_BOUNDARY_TO_FEATURES_BBOX_RATIO:
        return BoundaryPlausibilityCheck(
            verdict="implausible",
            boundary_area_m2=boundary_area,
            features_bbox_area_m2=features_bbox_area,
            ratio=ratio,
            explanation=(
                f"Площадь границы участка ({boundary_area:.1f} кв. м) подозрительно мала относительно "
                f"охвата (bounding box) всех объектов-коммуникаций на участке ({features_bbox_area:.1f} кв. м) "
                f"— отношение {ratio:.5f} ниже порога {_MIN_BOUNDARY_TO_FEATURES_BBOX_RATIO}. Вероятная "
                "причина — сбой автосборки границы из разрозненных отрезков (см. docs/DATA_STRUCTURE.md "
                "§11), а не то, что участок действительно настолько мал. Не доверять "
                "site_boundary_status='found' автоматически — проверить вручную."
            ),
        )

    return BoundaryPlausibilityCheck(
        verdict="ok",
        boundary_area_m2=boundary_area,
        features_bbox_area_m2=features_bbox_area,
        ratio=ratio,
        explanation=f"Площадь границы ({boundary_area:.1f} кв. м) правдоподобна относительно охвата коммуникаций.",
    )
