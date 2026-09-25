"""Сериализация shapely-геометрии в JSON для фронтенда (pipeline/web/static/).

Не GeoJSON намеренно — координаты локальные (метры внутренней системы
координат чертежа, не WGS84), полноценный GeoJSON вводил бы в заблуждение
насчёт системы координат. Простой формат {exterior, holes} на кольцо.
"""

from __future__ import annotations

from shapely.geometry.base import BaseGeometry


def _ring_to_points(ring) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in ring.coords]


def polygon_to_rings(geom: BaseGeometry) -> list[dict]:
    """Polygon/MultiPolygon -> список {"exterior": [[x,y],...], "holes": [[...],...]}.
    Пустая/иная геометрия (LineString и т.п. — не должно случиться для
    site_boundary/allowed_zone, но на всякий случай не падаем) -> []."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        rings: list[dict] = []
        for p in polys:
            rings.extend(polygon_to_rings(p))
        return rings
    else:
        return []

    return [
        {
            "exterior": _ring_to_points(p.exterior),
            "holes": [_ring_to_points(h) for h in p.interiors],
        }
        for p in polys
    ]


def geometry_bounds(*geoms: BaseGeometry) -> list[float] | None:
    """Общий bbox [minx, miny, maxx, maxy] нескольких геометрий (для viewBox
    на фронтенде) — None, если все геометрии пустые/отсутствуют."""
    boxes = [g.bounds for g in geoms if g is not None and not g.is_empty]
    if not boxes:
        return None
    minx = min(b[0] for b in boxes)
    miny = min(b[1] for b in boxes)
    maxx = max(b[2] for b in boxes)
    maxy = max(b[3] for b in boxes)
    return [minx, miny, maxx, maxy]
