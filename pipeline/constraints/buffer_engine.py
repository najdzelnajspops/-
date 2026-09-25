"""Constraint/Buffer Engine — построение карты «где можно сажать».

Отвечает за Этап 2 из docs/DATA_ALGORITHM.md: берёт границу участка и список
объектов-ограничений (коммуникации, охранные зоны, сохраняемые насаждения),
строит буферные зоны отступов и возвращает допустимую/запрещённую площадь —
каждая запрещённая зона с явной ссылкой на то, какая норма (или какая именно
запись перечетной ведомости) её породила.

Намеренно не связан с DXF/ezdxf — принимает уже разобранную геометрию (shapely),
не завязанную на конкретный формат входного файла.

Ключевой принцип проекта (см. AGENTS.md, docs/AGENTS_PLAN.md — агент ОТК):
не имитировать точность, которой нет. Если для типа границы нет верифицированной
нормы, движок это явно помечает (verified=False), а не тихо использует произвольное
число как будто оно проверено.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pipeline.constraints.offset_registry import OffsetRegistry, PlantingKind

# Число сегментов, которыми аппроксимируется четверть окружности при buffer().
# Зафиксировано явно, а не оставлено на дефолт библиотеки: дефолт shapely/GEOS
# менялся между версиями (quad_segs=8 в текущей, resolution=16 в старом API) и
# теоретически может измениться снова — при обязательном требовании повторяемости
# результата (docs/ARCHITECTURE.md §3.1) нельзя, чтобы обновление зависимости
# незаметно изменило форму буферной зоны. Значение 16 — компромисс между точностью
# аппроксимации окружности и объёмом геометрии; конкретное число менее важно, чем
# то, что оно зафиксировано и одинаково на dev-машине и в Docker-образе.
BUFFER_QUAD_SEGS = 16


@dataclass(frozen=True)
class ConstraintFeature:
    """Один объект-ограничение (коммуникация, охранная зона, сохраняемое дерево и т.п.).

    explicit_distance_m/explicit_citation: заполняются вызывающим кодом, когда отступ
    определяется не по offset_registry (простая карта boundary_type -> метры), а по
    более сложной логике — например, охранная зона ЛЭП зависит от класса напряжения
    (см. pipeline/constraints/lep_zones.py). Если заданы — движок использует их
    напрямую как verified=True, минуя обычный lookup по offset_registry.
    """

    id: str
    boundary_type: str
    geometry: BaseGeometry
    label: str | None = None
    explicit_distance_m: float | None = None
    explicit_citation: str | None = None


@dataclass(frozen=True)
class ZoneSource:
    """Провенанс одной буферной зоны — что именно её породило и насколько это проверено."""

    feature_id: str
    boundary_type: str
    boundary_label_ru: str
    distance_m: float
    verified: bool
    citation: str | None
    buffer_geometry: BaseGeometry


@dataclass(frozen=True)
class SkippedFeature:
    """Объект, для которого отступ этого вида посадки нормой не предусмотрен
    (это не ошибка и не пробел в данных — например, кустарнику у мачты освещения
    отступ в 743-ПП/Таблице 3.6.1 не задан, только дереву)."""

    feature_id: str
    boundary_type: str
    reason: str


@dataclass
class ConstraintMapResult:
    planting_kind: PlantingKind
    site_boundary: BaseGeometry
    allowed_zone: BaseGeometry
    forbidden_zone: BaseGeometry
    zone_sources: list[ZoneSource] = field(default_factory=list)
    skipped_features: list[SkippedFeature] = field(default_factory=list)
    unknown_boundary_types: set[str] = field(default_factory=set)

    def to_report_rows(self) -> list[dict]:
        """Строки для Interpretation Report Agent (см. docs/REPORT_DESIGN.md) —
        по одной на каждую применённую буферную зону."""
        return [
            {
                "feature_id": zs.feature_id,
                "boundary_type": zs.boundary_type,
                "boundary_label_ru": zs.boundary_label_ru,
                "distance_m": zs.distance_m,
                "verified": zs.verified,
                "citation": zs.citation if zs.verified else "[требует верификации]",
            }
            for zs in self.zone_sources
        ]


def build_constraint_map(
    site_boundary: BaseGeometry,
    features: list[ConstraintFeature],
    planting_kind: PlantingKind,
    registry: OffsetRegistry,
    unverified_fallback_m: float | None = None,
) -> ConstraintMapResult:
    """Строит карту допустимости для одного вида посадки (tree/shrub).

    unverified_fallback_m: отступ, применяемый к объектам с типом границы,
    которого нет в реестре норм (registry). Если None — такие объекты не
    буферизуются вовсе и попадают в unknown_boundary_types (осторожный отказ
    лучше, чем непроверенное число по умолчанию, см. AGENTS.md). Если задан —
    буферизуются, но с явной пометкой verified=False, что должно быть видно
    в отчёте пользователю, а не выглядеть как подтверждённая норма.
    """
    zone_sources: list[ZoneSource] = []
    skipped: list[SkippedFeature] = []
    unknown_types: set[str] = set()
    buffer_geoms: list[BaseGeometry] = []

    for feature in features:
        if feature.explicit_distance_m is not None:
            distance = feature.explicit_distance_m
            # verified — по наличию цитаты, а не по факту "распознали дистанцию":
            # explicit_distance_m используется и для консервативных, явно НЕ
            # проверенных допущений (например, ЛЭП с неизвестным классом напряжения
            # в dxf_ingest.py) — там explicit_citation сознательно оставлен None.
            verified = feature.explicit_citation is not None
            citation = feature.explicit_citation
            label_ru = feature.label or feature.boundary_type
            buf = feature.geometry.buffer(distance, quad_segs=BUFFER_QUAD_SEGS)
            buffer_geoms.append(buf)
            zone_sources.append(
                ZoneSource(
                    feature_id=feature.id,
                    boundary_type=feature.boundary_type,
                    boundary_label_ru=label_ru,
                    distance_m=distance,
                    verified=verified,
                    citation=citation,
                    buffer_geometry=buf,
                )
            )
            continue

        rule = registry.get_rule(feature.boundary_type)

        if rule is None:
            unknown_types.add(feature.boundary_type)
            if unverified_fallback_m is None:
                skipped.append(
                    SkippedFeature(
                        feature_id=feature.id,
                        boundary_type=feature.boundary_type,
                        reason="unknown_boundary_type_no_fallback",
                    )
                )
                continue
            distance = unverified_fallback_m
            verified = False
            citation = None
            label_ru = feature.boundary_type
        else:
            distance = rule.min_distance(planting_kind)
            if distance is None:
                skipped.append(
                    SkippedFeature(
                        feature_id=feature.id,
                        boundary_type=feature.boundary_type,
                        reason=f"no_offset_defined_for_{planting_kind}",
                    )
                )
                continue
            verified = True
            citation = registry.citation(feature.boundary_type)
            label_ru = rule.boundary_label_ru

        buf = feature.geometry.buffer(distance, quad_segs=BUFFER_QUAD_SEGS)
        buffer_geoms.append(buf)
        zone_sources.append(
            ZoneSource(
                feature_id=feature.id,
                boundary_type=feature.boundary_type,
                boundary_label_ru=label_ru,
                distance_m=distance,
                verified=verified,
                citation=citation,
                buffer_geometry=buf,
            )
        )

    if buffer_geoms:
        forbidden_zone = unary_union(buffer_geoms).intersection(site_boundary)
        allowed_zone = site_boundary.difference(forbidden_zone)
    else:
        forbidden_zone = GeometryCollection()
        allowed_zone = site_boundary

    return ConstraintMapResult(
        planting_kind=planting_kind,
        site_boundary=site_boundary,
        allowed_zone=allowed_zone,
        forbidden_zone=forbidden_zone,
        zone_sources=zone_sources,
        skipped_features=skipped,
        unknown_boundary_types=unknown_types,
    )
