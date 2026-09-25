"""DXF Ingest Agent — разбор DXF в геомодель Constraint Engine (Service A).

См. docs/DATA_ALGORITHM.md, Этап 1. Не работает с DWG напрямую — конвертация
DWG->DXF отдельным шагом (pipeline/ingest/dwg_convert.py), см.
docs/ARCHITECTURE.md §5.

ВАЖНО, найдено на реальном объекте (docs/DATA_STRUCTURE.md §9, обновление после
разбора «Олимпийская деревня»): главный файл «Генплан» ссылается на инженерные
коммуникации через XREF (внешние ссылки на соседние DWG в eTransmit-бандле) —
слои там видны в таблице слоёв документа, но фактическая геометрия НЕ читается
из самого главного файла без резолва XREF (ezdxf их не подгружает автоматически,
а сущности приходят как ACAD_PROXY_OBJECT). Реальные данные лежат в ОТДЕЛЬНЫХ
"компонентных" DWG того же бандла — файлы с суффиксом up (подземные коммуникации),
tp (топоплан), kl (красные линии) содержат геометрию напрямую, с ЧИСТЫМИ именами
слоёв (без префикса источника) — см. layer_name_aliases.yaml. Поэтому основной
контракт Ingest — набор файлов одного объекта (ingest_dxf_files), а не один файл.
Одиночный ingest_dxf() остаётся для случая, когда всё уже сведено в один DXF.

ДЕТЕРМИНИРОВАННОСТЬ (см. docs/ARCHITECTURE.md §3.1): сущности сортируются по
(имя_файла, DXF handle) перед возвратом результата — не полагаемся на порядок
обхода ezdxf/файловой системы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.ingest.layer_classifier import LayerClassification, LayerClassifier

# Типы DXF-сущностей, из которых мы умеем строить геометрию ограничения.
# Остальные (TEXT, MTEXT, DIMENSION, MULTILEADER, IMAGE, ACAD_TABLE, MLINE,
# REGION — см. docstring _hatch_to_geometry ниже) не являются геометрией
# ограничения сами по себе — попадают в skipped_entity_types, а не
# пропускаются молча без следа.
_SUPPORTED_ENTITY_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "INSERT", "POINT", "HATCH"}


@dataclass
class UnclassifiedLayerHit:
    layer_name: str
    category: str
    entity_count: int


@dataclass
class IngestResult:
    site_boundary: BaseGeometry | None
    site_boundary_status: str  # "found" | "not_found"
    features: list[ConstraintFeature] = field(default_factory=list)
    lep_features_needing_voltage: list[str] = field(default_factory=list)  # feature id-шники
    unclassified_layers: dict[str, UnclassifiedLayerHit] = field(default_factory=dict)
    ignored_entity_count: int = 0
    skipped_entity_types: set[str] = field(default_factory=set)
    source_files: list[str] = field(default_factory=list)


def _boundary_path_vertices(path) -> list[tuple[float, float]] | None:
    """Точки одного boundary path сущности HATCH — либо готовый список вершин
    (PolylinePath), либо начальные точки рёбер по порядку (EdgePath).

    Для EdgePath берём ТОЛЬКО `.start` каждого ребра, без учёта формы самого
    ребра — верно для LineEdge (единственный тип ребра, реально встреченный на
    датасете, см. docs/DATA_STRUCTURE.md — слой «Красные линии», ~17k рёбер,
    все LineEdge). Для ArcEdge/EllipseEdge/SplineEdge это огрубление (кривая
    сегмента заменяется хордой) — не проверено на реальных данных, честно
    задокументировано как ограничение, не скрыто."""
    vertices = getattr(path, "vertices", None)
    if vertices:
        return [(v[0], v[1]) for v in vertices]
    edges = getattr(path, "edges", None)
    if edges:
        coords = []
        for edge in edges:
            start = getattr(edge, "start", None)
            if start is None:
                return None
            coords.append((start.x, start.y))
        return coords
    return None


def _hatch_to_geometry(entity) -> BaseGeometry | None:
    """HATCH (заливка контура) -> Polygon.

    Найдено на реальном объекте («Камчатская улица», слой «Красные линии»,
    2026-09-17): область отрисована НЕ одним полигоном, а ~4000 мелких HATCH-
    плиток (площадь каждой ~0.1-3 м²) — суммарная площадь восстанавливается
    как объединение всех плиток на уровне buffer_engine (unary_union), здесь
    же — геометрия ОДНОЙ плитки.

    Если у HATCH несколько boundary path (острова/вырезы) — путь с наибольшей
    площадью считается внешним контуром, остальные вырезаются как дыры. Это
    эвристика по площади, а не по DXF-флагу пути (group code 92) — на реальных
    данных этого проекта каждый HATCH слоя «Красные линии» имеет РОВНО один
    path (проверено на всех ~4000), многопутевой случай не встречен и не
    протестирован на реальных данных.

    REGION (~4000 сущностей на том же слое, судя по количеству — вероятно,
    парная ACIS-заливка той же площади для отображения в разных версиях
    AutoCAD) сознательно НЕ поддерживается: REGION хранит геометрию как
    ACIS B-rep (бинарный формат твердотельного моделирования), а не как
    простой список вершин/рёбер — распарсить её через ezdxf без полноценного
    ACIS-движка нельзя. HATCH одного слоя уже даёт содержательное покрытие
    площади (см. docs/DATA_STRUCTURE.md) — не результат домысливания."""
    rings: list[Polygon] = []
    for path in entity.paths.paths:
        coords = _boundary_path_vertices(path)
        if coords is None or len(coords) < 3:
            continue
        try:
            ring = Polygon(coords)
        except Exception:
            continue
        if not ring.is_valid or ring.area == 0:
            continue
        rings.append(ring)
    if not rings:
        return None
    rings.sort(key=lambda r: r.area, reverse=True)
    exterior = rings[0]
    if len(rings) == 1:
        return exterior
    try:
        return Polygon(exterior.exterior.coords, holes=[r.exterior.coords for r in rings[1:]])
    except Exception:
        return exterior


def _entity_to_geometry(entity) -> BaseGeometry | None:
    dxftype = entity.dxftype()
    try:
        if dxftype == "HATCH":
            return _hatch_to_geometry(entity)
        if dxftype == "LINE":
            return LineString([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)])
        if dxftype == "LWPOLYLINE":
            points = [(p[0], p[1]) for p in entity.get_points()]
            if len(points) < 2:
                return None
            if entity.closed and len(points) >= 3:
                return Polygon(points)
            return LineString(points)
        if dxftype == "POLYLINE":
            points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            if len(points) < 2:
                return None
            if entity.is_closed and len(points) >= 3:
                return Polygon(points)
            return LineString(points)
        if dxftype == "CIRCLE":
            # Упрощение: центр как точка-маркер (колодец и т.п.), радиус не участвует
            # в буферизации отступа — сам объект точечный, буфер считается от центра.
            return Point(entity.dxf.center.x, entity.dxf.center.y)
        if dxftype == "INSERT":
            return Point(entity.dxf.insert.x, entity.dxf.insert.y)
        if dxftype == "POINT":
            return Point(entity.dxf.location.x, entity.dxf.location.y)
    except Exception:
        return None
    return None


# Порог покрытия для доверия результату polygonize() — см. докстринг ниже.
# Подобран не из формального расчёта, а из наблюдения на реальном объекте
# («Олимпийская деревня»): там polygonize собрал полигон площадью 47 кв.м из
# 588 отрезков, чей общий охват (bounding box) — сотни метров, т.е. purely
# локальное случайное замыкание нескольких отрезков, не периметр участка.
_BOUNDARY_COVERAGE_CONFIDENCE_THRESHOLD = 0.5


def _bbox_diagonal(bounds: tuple[float, float, float, float]) -> float:
    minx, miny, maxx, maxy = bounds
    return ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5


def _collect_insert_boundary_geometry(
    insert_entity,
    rank: int,
    direct_polygons: list[tuple[int, str, BaseGeometry, float]],
    lines_by_rank: dict[int, list[LineString]],
) -> None:
    """Разворачивает INSERT (блок-ссылка) на слое границы участка в геометрию его
    содержимого — см. вызывающий код в _find_site_boundary_in_doc. Если блок
    почему-то не резолвится (битая ссылка, отсутствующее определение) — тихо
    ничего не добавляет, а не падает: отсутствие вклада в кандидаты границы
    корректно приведёт к статусу not_found/found_low_confidence выше по стеку,
    что честно, а не маскирует проблему исключением."""
    try:
        sub_entities = list(insert_entity.virtual_entities())
    except Exception:
        return
    for i, sub in enumerate(sub_entities):
        geom = _entity_to_geometry(sub)
        if geom is None:
            continue
        if isinstance(geom, Polygon):
            direct_polygons.append((rank, f"{insert_entity.dxf.handle}:v{i}", geom, 1.0))
        elif isinstance(geom, LineString):
            lines_by_rank.setdefault(rank, []).append(geom)


def _find_site_boundary_in_doc(
    modelspace, classifier: LayerClassifier
) -> list[tuple[int, str, BaseGeometry, float]]:
    """Возвращает кандидатов (priority_rank, id, geometry, confidence) из ОДНОГО
    документа — вызывающий код объединяет кандидатов из всех файлов бандла.

    confidence: 1.0 для прямого замкнутого контура (LWPOLYLINE/POLYLINE closed —
    полностью доверяем). Для контуров, собранных через shapely.ops.polygonize()
    из разрозненных отрезков (реальная ситуация — см. docs/DATA_STRUCTURE.md §9:
    588 отдельных LINE на слое "Граница площадки" объекта «Олимпийская деревня»,
    ни один не замкнутый контур сам по себе) — confidence = отношение диагонали
    bbox найденного полигона к диагонали bbox ВСЕХ линий-кандидатов этого ранга.
    Если polygonize случайно замкнул маленький локальный кусок (типичный сбой на
    грязных реальных данных — например, дуги примыкания бордюра, а не сам
    периметр) — bbox результата будет намного меньше общего охвата линий, и это
    видно по низкому confidence, а не выдаётся молча за надёжный результат.
    """
    direct_polygons: list[tuple[int, str, BaseGeometry, float]] = []
    lines_by_rank: dict[int, list[LineString]] = {}

    for entity in modelspace:
        rank = classifier.site_boundary_priority_rank(entity.dxf.layer)
        if rank is None:
            continue
        if entity.dxftype() == "INSERT":
            # Реальный периметр на слое границы иногда оформлен как блок-ссылка,
            # не как линии напрямую (см. docs/DATA_STRUCTURE.md §11.1, объект
            # «Куликовская улица» — единственная сущность на слое "Граница заказа"
            # это INSERT). _entity_to_geometry() для INSERT даёт только точку
            # вставки — этого недостаточно для контура, поэтому здесь разворачиваем
            # блок через virtual_entities() (блок определён ЛОКАЛЬНО в этом файле,
            # не XREF — в отличие от §10, геометрия реально резолвится).
            _collect_insert_boundary_geometry(entity, rank, direct_polygons, lines_by_rank)
            continue
        geom = _entity_to_geometry(entity)
        if geom is None:
            continue
        if isinstance(geom, Polygon):
            direct_polygons.append((rank, entity.dxf.handle, geom, 1.0))
        elif isinstance(geom, LineString):
            lines_by_rank.setdefault(rank, []).append(geom)

    candidates: list[tuple[int, str, BaseGeometry, float]] = list(direct_polygons)

    ranks_with_direct_polygons = {rank for rank, _, _, _ in direct_polygons}
    for rank, lines in lines_by_rank.items():
        if rank in ranks_with_direct_polygons:
            continue  # уже есть нормальный явный контур на этом приоритетном слое
        full_diag = _bbox_diagonal(LineString([pt for line in lines for pt in line.coords]).bounds)
        assembled = list(polygonize(lines))
        for i, poly in enumerate(assembled):
            confidence = _bbox_diagonal(poly.bounds) / full_diag if full_diag > 0 else 0.0
            candidates.append((rank, f"polygonize#{i}", poly, confidence))

    return candidates


def _ingest_document(
    doc, source_tag: str, classifier: LayerClassifier, lep_fallback_width: float
) -> tuple[list[ConstraintFeature], list[str], dict[str, UnclassifiedLayerHit], set[str], int, list]:
    """Разбирает один открытый ezdxf-документ. source_tag — уникальный на весь
    бандл идентификатор файла (например его имя) — примешивается к handle, т.к.
    DXF handle уникален только ВНУТРИ одного файла, не между файлами бандла."""
    msp = doc.modelspace()

    features: list[ConstraintFeature] = []
    lep_needing_voltage: list[str] = []
    unclassified: dict[str, UnclassifiedLayerHit] = {}
    skipped_types: set[str] = set()
    ignored_count = 0

    entries: list[tuple[str, object, LayerClassification]] = []
    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype not in _SUPPORTED_ENTITY_TYPES:
            skipped_types.add(dxftype)
            continue
        classification = classifier.classify(entity.dxf.layer)
        entries.append((entity.dxf.handle, entity, classification))

    # ДЕТЕРМИНИРОВАННЫЙ порядок — сортировка по handle внутри файла.
    entries.sort(key=lambda e: e[0])

    for handle, entity, classification in entries:
        if classification.status == "ignored":
            ignored_count += 1
            continue

        if classification.status == "site_boundary_candidate":
            # Обрабатывается отдельно в _find_site_boundary_in_doc — здесь просто
            # не создаём ConstraintFeature и не заносим в unclassified (это не
            # "непонятый" слой, а сознательно узнанный и обрабатываемый иначе).
            continue

        if classification.status == "unclassified":
            hit = unclassified.get(classification.category)
            if hit is None:
                unclassified[classification.category] = UnclassifiedLayerHit(
                    layer_name=classification.raw_layer_name,
                    category=classification.category,
                    entity_count=1,
                )
            else:
                hit.entity_count += 1
            continue

        geom = _entity_to_geometry(entity)
        if geom is None:
            continue

        feature_id = f"{source_tag}:h{handle}"

        if classification.status == "lep_unknown_voltage":
            features.append(
                ConstraintFeature(
                    id=feature_id,
                    boundary_type="power_line_overhead_unknown_voltage",
                    geometry=geom,
                    label="ЛЭП (класс напряжения не определён по данным чертежа)",
                    explicit_distance_m=lep_fallback_width,
                    explicit_citation=None,
                )
            )
            lep_needing_voltage.append(feature_id)
            continue

        features.append(
            ConstraintFeature(id=feature_id, boundary_type=classification.boundary_type, geometry=geom)
        )

    boundary_candidates = _find_site_boundary_in_doc(msp, classifier)
    boundary_candidates_tagged = [
        (rank, f"{source_tag}:{handle}", geom, confidence)
        for rank, handle, geom, confidence in boundary_candidates
    ]

    return features, lep_needing_voltage, unclassified, skipped_types, ignored_count, boundary_candidates_tagged


def ingest_dxf(path: str | Path, classifier: LayerClassifier | None = None) -> IngestResult:
    """Разбор ОДНОГО DXF-файла. Для реальных объектов датасета обычно нужен
    ingest_dxf_files() — см. предупреждение в докстринге модуля."""
    return ingest_dxf_files([path], classifier=classifier)


def ingest_dxf_files(paths: list[str | Path], classifier: LayerClassifier | None = None) -> IngestResult:
    """Разбор НАБОРА DXF-файлов одного объекта (главный файл + up/tp/kl-компоненты
    eTransmit-бандла) как единой геомодели. Порядок paths не влияет на результат —
    объекты сортируются по (имя_файла, handle) внутри объединения, см. ниже."""
    classifier = classifier or LayerClassifier()
    lep_registry = LepZoneRegistry()
    lep_fallback_width = lep_registry.most_conservative_width_m()

    all_features: list[ConstraintFeature] = []
    all_lep_needing: list[str] = []
    all_unclassified: dict[str, UnclassifiedLayerHit] = {}
    all_skipped: set[str] = set()
    all_ignored = 0
    all_boundary_candidates: list[tuple[int, str, BaseGeometry, float]] = []
    source_files: list[str] = []

    # Сортируем сами пути — чтобы объединённый результат не зависел от того, в
    # каком порядке вызывающий код перечислил файлы бандла.
    for path in sorted(str(p) for p in paths):
        source_tag = Path(path).name
        source_files.append(source_tag)
        doc = ezdxf.readfile(path)
        (
            features,
            lep_needing,
            unclassified,
            skipped,
            ignored,
            boundary_candidates,
        ) = _ingest_document(doc, source_tag, classifier, lep_fallback_width)

        all_features.extend(features)
        all_lep_needing.extend(lep_needing)
        for category, hit in unclassified.items():
            existing = all_unclassified.get(category)
            if existing is None:
                all_unclassified[category] = hit
            else:
                existing.entity_count += hit.entity_count
        all_skipped |= skipped
        all_ignored += ignored
        all_boundary_candidates.extend(boundary_candidates)

    # Финальная сортировка по (имя_файла, id) — детерминированный порядок
    # объединённого результата вне зависимости от порядка входных путей.
    all_features.sort(key=lambda f: f.id)

    if all_boundary_candidates:
        # ВАЖНО (найдено 2026-09-15 на реальных объектах — «Олимпийская деревня»,
        # «Грузинская М ул»): приоритет по РАНГУ ИМЕНИ СЛОЯ не должен перебивать
        # надёжность геометрии. Если объединить весь бандл (up+tp+brd), у части
        # объектов слой более высокого по рангу имени (например «Граница площадки»
        # в tp.dwg) даёт РАЗРОЗНЕННЫЕ отрезки → низкую confidence через
        # polygonize(), а слой более низкого по рангу имени (например «Граница
        # заказа» в brd.dwg) — ЧИСТЫЙ замкнутый контур, confidence=1.0. Старая
        # логика («сначала лучший ранг, затем внутри него лучшая confidence»)
        # в этом случае выбирала мусорный мелкий полигон вместо надёжного —
        # объект тихо получал `found_low_confidence` вместо `found`, хотя
        # надёжный контур физически присутствовал в бандле. Исправлено: сначала
        # ищем кандидатов с confidence >= порога СРЕДИ ВСЕХ рангов; ранг остаётся
        # лишь тай-брейком между одинаково надёжными кандидатами. Только если
        # НИ ОДИН кандидат не надёжен — используется прежнее поведение (лучший
        # ранг среди ненадёжных, статус честно `found_low_confidence`).
        reliable = [c for c in all_boundary_candidates if c[3] >= _BOUNDARY_COVERAGE_CONFIDENCE_THRESHOLD]
        pool = reliable if reliable else all_boundary_candidates

        best_rank = min(rank for rank, _, _, _ in pool)
        at_best_rank = [c for c in pool if c[0] == best_rank]
        # Внутри лучшего по приоритету слоя выбираем кандидата с НАИБОЛЬШИМ
        # confidence (см. _find_site_boundary_in_doc), затем по площади, затем
        # по id — детерминированный порядок при равенстве (docs/ARCHITECTURE.md §3.1).
        at_best_rank.sort(key=lambda c: (-c[3], -c[2].area, c[1]))
        site_boundary = at_best_rank[0][2]
        best_confidence = at_best_rank[0][3]
        if best_confidence < _BOUNDARY_COVERAGE_CONFIDENCE_THRESHOLD:
            # Геометрия возвращается (лучше, чем ничего, для ручной проверки), но
            # статус явно отделён от надёжного "found" — не должен молча приниматься
            # автоматикой (Constraint Engine) как достоверная граница участка.
            site_boundary_status = "found_low_confidence"
        else:
            site_boundary_status = "found"
    else:
        site_boundary = None
        site_boundary_status = "not_found"

    return IngestResult(
        site_boundary=site_boundary,
        site_boundary_status=site_boundary_status,
        features=all_features,
        lep_features_needing_voltage=sorted(all_lep_needing),
        unclassified_layers=all_unclassified,
        ignored_entity_count=all_ignored,
        skipped_entity_types=all_skipped,
        source_files=source_files,
    )
