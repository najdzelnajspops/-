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

import numpy as np
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from pipeline.constraints.buffer_engine import ConstraintFeature

# Если площадь границы участка меньше этой доли от площади bounding box всех
# объектов-коммуникаций на участке — подозрительно мало для реального участка.
# Порог подобран не формальным расчётом, а по наблюдению на реальном случае
# (см. докстринг модуля): корректная граница «Олимпийской деревни» — 494 315 м²,
# дефектная — 46,9 м² (в ~10000 раз меньше охвата коммуникаций) — разница на
# порядки, порог 1% даёт большой запас, не подгонка под конкретное число.
_MIN_BOUNDARY_TO_FEATURES_BBOX_RATIO = 0.01

# Запас вокруг границы при проверке «объекты лежат на участке»: коммуникации могут идти вдоль самой
# границы и чуть за ней. Ноль объектов в этой полосе — граница и коммуникации в разных системах
# координат либо граница не от этого участка (найдено 2026-10-19: «Харьковская улица», граница из
# проектного чертежа не пересекалась с геодезией, а проверка площади отвечала «ok»).
_ALIGNMENT_MARGIN_M = 50.0

# Охват объектов считается без самых далёких: несколько заблудившихся объектов (например, в нулевой точке
# чертежа) раздували охват в сотни раз и давали ложное «implausible» на верной границе (найдено 2026-10-19:
# «Кустанайская улица» 0,0047, «Грузинская М ул» 0,0006; без 0,5 % с каждой стороны 0,39 и 0,031 при пороге
# 0,01). Настоящая ошибка (граница в сотни раз меньше охвата) остаётся далеко ниже порога. При малом числе
# объектов усечения нет: там выбросы нельзя отличить от настоящего охвата.
_OUTLIER_TRIM_FRACTION = 0.005
_MIN_FEATURES_FOR_TRIMMING = 200

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
    на том же участке. Это ЭВРИСТИКА для привлечения внимания пользователя, а не
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

    # 2026-09-27: раньше здесь считался unary_union() ради одного лишь bounds —
    # на реальном объекте («Макеева С. ул», 104 130 объектов) это ~30 секунд
    # чистых накладных расходов на полное геометрическое объединение, хотя
    # bbox объединения МАТЕМАТИЧЕСКИ идентичен bbox, собранному из min/max
    # отдельных bounds (объединение не меняет общий охват) — не приближение,
    # тот же результат в 1000+ раз быстрее (проверено: 1.3 сек на том же объекте).
    feature_bounds = np.array([f.geometry.bounds for f in features])
    if len(features) >= _MIN_FEATURES_FOR_TRIMMING:
        minx, miny = np.quantile(feature_bounds[:, 0], _OUTLIER_TRIM_FRACTION), np.quantile(feature_bounds[:, 1], _OUTLIER_TRIM_FRACTION)
        maxx, maxy = np.quantile(feature_bounds[:, 2], 1 - _OUTLIER_TRIM_FRACTION), np.quantile(feature_bounds[:, 3], 1 - _OUTLIER_TRIM_FRACTION)
    else:
        minx, miny = feature_bounds[:, 0].min(), feature_bounds[:, 1].min()
        maxx, maxy = feature_bounds[:, 2].max(), feature_bounds[:, 3].max()
    minx, miny, maxx, maxy = float(minx), float(miny), float(maxx), float(maxy)
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
                "причина — сбой автосборки границы из разрозненных отрезков, а не то, что "
                "участок действительно настолько мал. Статус «граница найдена» не стоит принимать "
                "на веру — проверьте границу участка."
            ),
        )

    # Совмещение: хотя бы один объект должен лежать на участке (с запасом). Сначала дешёвая проверка
    # по рамкам, затем точная по индексу — на объекте в 140 тысяч объектов это порядка секунды.
    zone = site_boundary.buffer(_ALIGNMENT_MARGIN_M)
    zx0, zy0, zx1, zy1 = zone.bounds
    # для быстрой проверки по рамке нужен ПОЛНЫЙ охват (усечённый мог бы отсечь границу у самого края)
    fminx, fminy = float(feature_bounds[:, 0].min()), float(feature_bounds[:, 1].min())
    fmaxx, fmaxy = float(feature_bounds[:, 2].max()), float(feature_bounds[:, 3].max())
    bbox_overlaps = not (zx1 < fminx or zx0 > fmaxx or zy1 < fminy or zy0 > fmaxy)
    if bbox_overlaps:
        nearby = len(STRtree([f.geometry for f in features]).query(zone, predicate="intersects"))
    else:
        nearby = 0
    if nearby == 0:
        return BoundaryPlausibilityCheck(
            verdict="implausible",
            boundary_area_m2=boundary_area,
            features_bbox_area_m2=features_bbox_area,
            ratio=ratio,
            explanation=(
                f"Граница участка ({boundary_area:.1f} кв. м) не пересекается ни с одним из {len(features)} "
                "объектов-коммуникаций (даже с запасом 50 м): граница и коммуникации, вероятно, в разных "
                "системах координат либо граница взята из другого чертежа. Посадки при такой границе "
                "ненадёжны — загрузите границу из того же комплекта, что и коммуникации."
            ),
        )

    return BoundaryPlausibilityCheck(
        verdict="ok",
        boundary_area_m2=boundary_area,
        features_bbox_area_m2=features_bbox_area,
        ratio=ratio,
        explanation=f"Площадь границы ({boundary_area:.1f} кв. м) правдоподобна относительно охвата коммуникаций.",
    )
