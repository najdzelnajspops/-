"""Агент ОТК, проверка 6.1 (продолжение) — независимая сверка отступов (Service A).

См. docs/AGENTS_PLAN.md, агент №6, проверка 6.1: «независимый пересчёт расстояний
напрямую по исходной геометрии, не через внутренние переменные генератора».
`pipeline/otk/boundary_plausibility.py` (6.1a) уже проверяет ПРАВДОПОДОБИЕ ПЛОЩАДИ
границы участка в целом; эта проверка — про КАЖДУЮ отдельную точку посадки: для
каждой точки заново считает фактическое расстояние Point.distance(feature.geometry)
до КАЖДОГО объекта-ограничения и требуемый отступ, сравнивая их напрямую — а не
полагаясь на то, что точка прошла `allowed_zone.contains()` где-то внутри генератора.

Ловит класс багов, которые 6.1a не поймала бы: например, ошибку в
`build_constraint_map` (не тот знак буфера, объединение не тех геометрий,
перепутанные единицы измерения), при которой `allowed_zone` получилась бы
неверной, но самосогласованной со своими же переменными — сравнение ПЛОЩАДЕЙ
(6.1a) такое не заметит, а прямая проверка расстояния точка-объект заметит.

ПРИНЦИП НЕЗАВИСИМОСТИ (обязателен для агента ОТК, см. AGENTS_PLAN.md): этот
модуль НЕ импортирует `build_constraint_map` и не читает `ConstraintMapResult`
(ни `allowed_zone`/`forbidden_zone`, ни `zone_sources` — все вычисленные Constraint
Engine величины). Единственные входы — те же сырые данные, что получает сам
`build_constraint_map` (site_boundary, список ConstraintFeature с исходной,
НЕбуферизованной геометрией), справочник норм `OffsetRegistry` (источник данных,
общий для генератора и проверки — не вычисленный генератором результат, а
таблица, откуда генератор сам их берёт: дублировать эти цифры отдельным кодом
было бы хуже — риск разъехаться с оригиналом) и уже размещённые точки
(`PlacementPoint` — конечный результат, который в любом случае обязан быть
проверяем независимо от механизма получения).

ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ: объекты с неизвестным реестру типом границы (см.
`OffsetRegistry.is_known`) или без заданного отступа для данной жизненной формы
пропускаются проверкой — так же, как их пропускает `build_constraint_map`
(см. `skipped_features`/`unknown_boundary_types` там же). Эта проверка не может
поймать отсутствие нормы там, где её действительно нет ни у кого — она сверяет
только те отступы, которые формально применимы.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.offset_registry import OffsetRegistry, PlantingKind
from pipeline.placement.generator import PlacementPoint

# Допуск на геометрическую погрешность аппроксимации окружности буфером
# (BUFFER_QUAD_SEGS сегментов на четверть окружности, buffer_engine.py) — максимальная
# сагитта r*(1-cos(pi/(4*BUFFER_QUAD_SEGS))) для реалистичных отступов (<=50 м)
# не превышает нескольких сантиметров; порог явно шире как запас. Природа допуска —
# форма буферного полигона, а не толкование нормы (см. также docstring модуля).
_BUFFER_APPROXIMATION_TOLERANCE_M = 0.15


@dataclass(frozen=True)
class OffsetViolation:
    point_id: str
    kind: str  # "outside_site_boundary" | "insufficient_offset"
    feature_id: str | None
    boundary_type: str | None
    required_distance_m: float | None
    actual_distance_m: float
    detail: str


@dataclass
class OffsetComplianceCheck:
    verdict: str  # "ok" | "violation" | "not_checked"
    violations: list[OffsetViolation] = field(default_factory=list)
    points_checked: int = 0
    explanation: str = ""


def _required_distance(
    feature: ConstraintFeature, registry: OffsetRegistry, planting_kind: PlantingKind
) -> float | None:
    if feature.explicit_distance_m is not None:
        return feature.explicit_distance_m
    rule = registry.get_rule(feature.boundary_type)
    return rule.min_distance(planting_kind) if rule is not None else None


def check_offset_compliance(
    site_boundary: BaseGeometry,
    features: list[ConstraintFeature],
    registry: OffsetRegistry,
    planting_kind: PlantingKind,
    points: list[PlacementPoint],
) -> OffsetComplianceCheck:
    """Независимая сверка: у каждой точки посадки данного вида — фактическое
    расстояние до каждого объекта-ограничения не меньше требуемого отступа, и
    сама точка внутри границы участка. Использует пространственный индекс
    (STRtree) — практическая необходимость, не просто оптимизация: реальные
    объекты датасета дают десятки тысяч объектов-ограничений (например,
    «Грузинская М ул» — 85 411, см. docs/SESSION_LOG.md), полный перебор
    точка×объект был бы неоправданно медленным."""
    relevant_points = [p for p in points if p.planting_kind == planting_kind]
    if not relevant_points:
        return OffsetComplianceCheck(
            verdict="not_checked",
            explanation="Нет точек посадки этого вида для проверки.",
        )

    relevant_features: list[tuple[ConstraintFeature, float]] = []
    for feature in features:
        required = _required_distance(feature, registry, planting_kind)
        if required is not None:
            relevant_features.append((feature, required))

    violations: list[OffsetViolation] = []
    point_geoms = [Point(p.x, p.y) for p in relevant_points]

    for point, pt in zip(relevant_points, point_geoms):
        boundary_distance = site_boundary.distance(pt)
        if boundary_distance > _BUFFER_APPROXIMATION_TOLERANCE_M and not site_boundary.contains(pt):
            violations.append(
                OffsetViolation(
                    point_id=point.id,
                    kind="outside_site_boundary",
                    feature_id=None,
                    boundary_type=None,
                    required_distance_m=None,
                    actual_distance_m=boundary_distance,
                    detail=(
                        f"Точка {point.id} ({point.x:.2f}, {point.y:.2f}) вне границы участка "
                        f"(расстояние {boundary_distance:.2f} м)."
                    ),
                )
            )

    if relevant_features:
        max_required = max(required for _, required in relevant_features)
        query_radius = max_required + _BUFFER_APPROXIMATION_TOLERANCE_M
        tree = STRtree([feature.geometry for feature, _ in relevant_features])
        point_idx, feature_idx = tree.query(point_geoms, predicate="dwithin", distance=query_radius)
        for pi, fi in zip(point_idx, feature_idx):
            point = relevant_points[pi]
            pt = point_geoms[pi]
            feature, required = relevant_features[fi]
            actual = pt.distance(feature.geometry)
            if actual + _BUFFER_APPROXIMATION_TOLERANCE_M < required:
                violations.append(
                    OffsetViolation(
                        point_id=point.id,
                        kind="insufficient_offset",
                        feature_id=feature.id,
                        boundary_type=feature.boundary_type,
                        required_distance_m=required,
                        actual_distance_m=actual,
                        detail=(
                            f"Точка {point.id} на расстоянии {actual:.2f} м от объекта "
                            f"«{feature.label or feature.boundary_type}» (id={feature.id}), "
                            f"требуется не менее {required:.2f} м."
                        ),
                    )
                )

    verdict = "ok" if not violations else "violation"
    explanation = (
        f"Независимая проверка {len(relevant_points)} точек посадки вида «{planting_kind}»: "
        "фактическое расстояние до каждого объекта-ограничения соответствует требуемому отступу."
        if verdict == "ok"
        else f"Найдено нарушений отступа: {len(violations)} — см. violations."
    )
    return OffsetComplianceCheck(
        verdict=verdict,
        violations=violations,
        points_checked=len(relevant_points),
        explanation=explanation,
    )
