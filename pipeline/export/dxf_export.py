"""DXF Export Agent — запись результата Placement Generator в отдельные слои DXF.

См. docs/AGENTS_PLAN.md, агент №7; docs/TASK_BRIEF.md, п.3 (жёсткое требование
ТЗ): результат — ТОЛЬКО на отдельном(-ых) слое(-ях), исходные слои/сущности не
меняются и не перезаписываются.

MVP-ограничение (см. docs/OPEN_QUESTIONS.md): не пытается пересобрать весь
eTransmit-бандл объекта (несколько DWG/DXF-компонентов, см.
pipeline/ingest/dxf_ingest.py) в один выходной файл. Открывает ОДИН базовый
DXF-файл объекта (как правило — топоплан/файл с границей участка, тот же,
что дал site_boundary при Ingest) и добавляет в НЕГО новые слои, сохраняя
результат в НОВЫЙ файл (base_dxf_path никогда не перезаписывается — гарантируется
явной проверкой пути на входе, а не только соглашением по факту вызова saveas).

Условные обозначения — по ГОСТ 21.508-2020 (см. docs/NORMATIVE_REFERENCES.md,
раздел про этот акт): дерево — окружность с крестом внутри, кустарник —
компактная группа рисуется единым «пятном-массой» с одной подписью на группу
(не точка на экземпляр — плотная группа в десятки точек была бы нечитаема на
чертеже, тот же принцип уже применялся в ручном прототипе-визуализации,
см. docs/CHECKLIST.md, 2026-09-16/17). Раньше (до 2026-09-23) здесь рисовались
голые POINT+TEXT без всякой стилизации — этот файл был реальным разрывом между
«умеем красиво» (только в прототипе вне пайплайна) и «реально выгружаем».

РЕАЛЬНЫЕ БЛОКИ ОРГАНИЗАТОРОВ (2026-09-24): docs/DATA_STRUCTURE.md, §8 прямо
фиксирует задачу «подтягивать блоки из „Шаблоны значков.dwg“ для отрисовки
предложенных посадок в едином со стандартом ДПиООС стиле, а не рисовать свои
произвольные значки». Файл-источник — на деле полноценный образцовый объект
организаторов (~130 блоков, в основном по конкретным видам), но среди них есть
ровно 3 универсальных шаблонных блока по нашей же классификации
(лиственное/хвойное дерево, кустарник): 'Дерево_Л_С', 'Дерево_Х_С', 'Куст_Л_С'.
Эти 3 определения извлечены разовым скриптом (ezdxf.addons.importer.Importer,
import_block по одному блоку + finalize) в отдельный маленький git-файл
`data/reference/planting_symbols.dxf` — не тянем в репозиторий весь
12-мегабайтный образцовый объект ради 3 блоков. Сам скрипт извлечения не
checked-in (в проекте нет каталога `scripts/` для одноразовых утилит) —
воспроизводится по этому докстрингу при необходимости повторить экстракцию.
Используются ТОЛЬКО для деревьев со статусом "placed" (для них известен
`chosen_species_entry.life_form_group_ru`, что даёт хвойное/лиственное) —
блок выбирается по этому признаку (`_pick_tree_symbol_source_block`).
Для отклонённых точек ("rejected") вид не выбирается в принципе (см.
generator.py) — там по-прежнему рисуется собственный круг-с-крестом
(`_draw_tree_symbol`), т.к. хвойное/лиственное неизвестно. Кустарник
по-прежнему рисуется своим «пятном-массой» (`_draw_shrub_mass`) — это не
«произвольный значок», а честная геометрическая проекция реальных точек
группы, что не хуже удовлетворяет тому же принципу, а собственных
кустарниковых блоков-шаблонов организаторов без хвойного варианта всё равно
только один и он не даёт read ничего сверх уже отрисовываемой массы.

Слои: внутренние сущности импортируемого блока в исходном файле лежат на ЧУЖИХ
слоях (`СП_дендроплан`, `СП_зеленые насаждения для ГП`, `Defpoints`) — это
нарушило бы собственное же требование проекта (агент ОТК 6.2,
pipeline/otk/layer_integrity.py) о том, что весь новый контент — только в
слоях `GREEN_AI_*`. Поэтому после импорта определения блока все его сущности
явно перепривязываются на `GREEN_AI_TREE_PROPOSED`
(`_import_remapped_tree_block`), а неиспользуемые более исходные слои
удаляются из итогового документа, чтобы проверка целостности слоёв видела
только ожидаемые новые слои.

ВАЖНАЯ ОГОВОРКА ПО ТРАССИРУЕМОСТИ: `PlacementPoint` не хранит `cluster_id` —
группировка кустарника для отрисовки делается здесь же, чисто геометрически
(см. `_cluster_shrub_points`), не меняя модель данных генератора. Значит для
деревьев id на чертеже совпадает 1:1 с id в JSON-отчёте (как и раньше), а для
кустарника трассируемость — на уровне ГРУППЫ (метка группы на чертеже ↔
координаты входящих в неё точек в отчёте), а не отдельного экземпляра — это
осознанный компромисс в пользу читаемости чертежа, соответствующий самому
ГОСТ 21.508-2020 (там кустарник тоже подписывается ведомостью по группе, не
поточечно), см. docs/LIMITATIONS.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.addons.importer import Importer

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesEntry
from pipeline.constraints.offset_registry import PlantingKind
from pipeline.placement.generator import PlacementPoint, PlacementResult

# См. докстринг модуля, раздел "РЕАЛЬНЫЕ БЛОКИ ОРГАНИЗАТОРОВ" — 3 универсальных
# шаблонных блока, извлечённые из "Шаблоны значков.dwg".
SYMBOL_LIBRARY_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "planting_symbols.dxf"
CONIFER_TREE_SOURCE_BLOCK = "Дерево_Х_С"
DECIDUOUS_TREE_SOURCE_BLOCK = "Дерево_Л_С"

LAYER_NAMES: dict[tuple[PlantingKind, str], str] = {
    ("tree", "placed"): "GREEN_AI_TREE_PROPOSED",
    ("tree", "rejected"): "GREEN_AI_TREE_REJECTED",
    ("shrub", "placed"): "GREEN_AI_SHRUB_PROPOSED",
    ("shrub", "rejected"): "GREEN_AI_SHRUB_REJECTED",
}

# Цвета по ACI (AutoCAD Color Index) — только для удобства просмотра в CAD,
# не несут нормативного смысла. Зелёный = предложено, красный = отклонено.
LAYER_COLORS: dict[tuple[PlantingKind, str], int] = {
    ("tree", "placed"): 3,  # green
    ("tree", "rejected"): 1,  # red
    ("shrub", "placed"): 92,  # yellow-green
    ("shrub", "rejected"): 1,
}

TEXT_HEIGHT_M = 0.4

# Радиус условного знака дерева (окружность с крестом, ГОСТ 21.508-2020) —
# ориентировочный визуальный размер кроны/ствола на чертеже, не норма отступа
# (та считается отдельно в pipeline/constraints/offset_registry.py). Подобран
# заметно меньше типичного шага между деревьями (743-ПП, Таблица 3.6.2:
# 3-6 м), чтобы знаки не перекрывались на реальной плотности посадки.
TREE_SYMBOL_RADIUS_M = 0.6

# Запас поверх реального расстояния внутри группы кустарника
# (PlacementPoint.grid_spacing_m = ClusterConfig.intra_group_spacing_m,
# см. pipeline/placement/generator.py) при кластеризации точек ТОЛЬКО для
# отрисовки — с запасом чуть больше диагонали шага сетки (√2), чтобы не
# разорвать группу из-за плавающей точки, но заметно меньше типичного
# расстояния МЕЖДУ группами (иначе разные группы слились бы в одну).
SHRUB_CLUSTER_DISTANCE_FACTOR = 1.6
SHRUB_MASS_BUFFER_M = 0.3  # визуальный отступ контура "пятна" за крайние кусты группы


class BaseFileWouldBeOverwrittenError(ValueError):
    pass


@dataclass
class LayerExportSummary:
    layer_name: str
    entity_count: int


@dataclass
class ExportSummary:
    output_path: str
    base_dxf_path: str
    layers: list[LayerExportSummary] = field(default_factory=list)

    def total_entities(self) -> int:
        return sum(layer.entity_count for layer in self.layers)


def _ensure_layer(doc, layer_name: str, color: int) -> None:
    if layer_name not in doc.layers:
        doc.layers.add(name=layer_name, color=color)


def _pick_tree_symbol_source_block(chosen_species_entry: DpioosSpeciesEntry | None) -> str:
    """Хвойное/лиственное — по `life_form_group_ru` (тот же признак и та же
    подстрока "хвойн", что в pipeline/catalog/price_list_ingest.py и
    pipeline/catalog/planting_cost.py — не изобретаем новый способ классификации)."""
    if chosen_species_entry is not None and "хвойн" in chosen_species_entry.life_form_group_ru.lower():
        return CONIFER_TREE_SOURCE_BLOCK
    return DECIDUOUS_TREE_SOURCE_BLOCK


def _import_remapped_tree_block(doc, source_block_name: str, target_layer: str, cache: dict[tuple[int, str, str], str]) -> str:
    """Импортирует блок дерева из SYMBOL_LIBRARY_PATH в `doc`, перепривязывая
    ВСЕ его внутренние сущности на `target_layer` (см. докстринг модуля, раздел
    про слои) — кэшируется per (id(doc), source_block_name, target_layer), чтобы
    не читать файл-источник и не создавать повторное определение блока на
    каждую точку."""
    cache_key = (id(doc), source_block_name, target_layer)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    source_doc = ezdxf.readfile(str(SYMBOL_LIBRARY_PATH))
    layers_before = {layer.dxf.name for layer in doc.layers}

    importer = Importer(source_doc, doc)
    local_block_name = importer.import_block(source_block_name, rename=True)
    importer.finalize()

    block = doc.blocks.get(local_block_name)
    for entity in block:
        entity.dxf.layer = target_layer

    # Слои, добавленные импортом (исходные слои блока вроде "СП_дендроплан",
    # "Defpoints") больше не используются — все сущности блока перепривязаны
    # на target_layer. Убираем их, чтобы агент ОТК 6.2 не увидел лишних новых
    # слоёв, не начинающихся с GREEN_AI_ (см. pipeline/otk/layer_integrity.py).
    layers_after = {layer.dxf.name for layer in doc.layers}
    for stale_layer_name in layers_after - layers_before:
        doc.layers.remove(stale_layer_name)

    cache[cache_key] = local_block_name
    return local_block_name


def _draw_tree_symbol(msp, x: float, y: float, layer_name: str) -> None:
    """Условный знак дерева по ГОСТ 21.508-2020 — окружность с крестом внутри."""
    r = TREE_SYMBOL_RADIUS_M
    msp.add_circle(center=(x, y), radius=r, dxfattribs={"layer": layer_name})
    msp.add_line((x - r, y), (x + r, y), dxfattribs={"layer": layer_name})
    msp.add_line((x, y - r), (x, y + r), dxfattribs={"layer": layer_name})


def _cluster_shrub_points(points: list[PlacementPoint]) -> list[list[PlacementPoint]]:
    """Группирует точки кустарника ТОЛЬКО для отрисовки (не меняет данные
    генератора, см. докстринг модуля) — простая односвязная кластеризация по
    расстоянию: точка присоединяется к группе, если она в пределах порога хотя
    бы от одного уже включённого в группу соседа (устойчиво к вытянутым
    группам, не только к строго круглым)."""
    if not points:
        return []
    threshold = points[0].grid_spacing_m * SHRUB_CLUSTER_DISTANCE_FACTOR
    remaining = list(points)
    clusters: list[list[PlacementPoint]] = []
    while remaining:
        cluster = [remaining.pop()]
        grown = True
        while grown:
            grown = False
            for p in remaining[:]:
                if any(math.dist((p.x, p.y), (c.x, c.y)) <= threshold for c in cluster):
                    cluster.append(p)
                    remaining.remove(p)
                    grown = True
        clusters.append(cluster)
    return clusters


def _draw_shrub_mass(msp, cluster: list[PlacementPoint], group_id: str, layer_name: str) -> None:
    """Условный знак кустарника по ГОСТ 21.508-2020 — контур «пятна-массы»
    вокруг группы + одна подпись с числом экземпляров (не точка на каждый
    куст — см. докстринг модуля)."""
    if len(cluster) == 1:
        p = cluster[0]
        r = SHRUB_MASS_BUFFER_M + 0.2
        msp.add_circle(center=(p.x, p.y), radius=r, dxfattribs={"layer": layer_name})
        cx, cy = p.x, p.y
    else:
        from shapely.geometry import MultiPoint

        hull = MultiPoint([(p.x, p.y) for p in cluster]).convex_hull.buffer(SHRUB_MASS_BUFFER_M)
        exterior_coords = list(hull.exterior.coords)
        msp.add_lwpolyline(exterior_coords, close=True, dxfattribs={"layer": layer_name})
        centroid = hull.centroid
        cx, cy = centroid.x, centroid.y

    msp.add_text(
        f"{group_id} ({len(cluster)} шт.)",
        dxfattribs={"layer": layer_name, "height": TEXT_HEIGHT_M, "insert": (cx, cy)},
    )


def export_placement_to_dxf(
    base_dxf_path: str | Path,
    output_path: str | Path,
    placement_results: list[PlacementResult],
) -> ExportSummary:
    base_dxf_path = Path(base_dxf_path)
    output_path = Path(output_path)

    if base_dxf_path.resolve() == output_path.resolve():
        raise BaseFileWouldBeOverwrittenError(
            f"output_path совпадает с base_dxf_path ({base_dxf_path}) — исходный файл "
            "не должен быть перезаписан результатом (см. docs/TASK_BRIEF.md, п.3.1)."
        )

    doc = ezdxf.readfile(str(base_dxf_path))
    msp = doc.modelspace()

    layer_counts: dict[str, int] = {}
    tree_block_cache: dict[tuple[int, str, str], str] = {}

    for result in placement_results:
        for status_key, points in (
            ("placed", result.placed_points()),
            ("rejected", result.rejected_points()),
        ):
            if not points:
                continue
            layer_name = LAYER_NAMES[(result.planting_kind, status_key)]
            _ensure_layer(doc, layer_name, LAYER_COLORS[(result.planting_kind, status_key)])
            layer_counts.setdefault(layer_name, 0)

            if result.planting_kind == "tree":
                block_name = None
                if status_key == "placed":
                    source_block = _pick_tree_symbol_source_block(result.chosen_species_entry)
                    block_name = _import_remapped_tree_block(doc, source_block, layer_name, tree_block_cache)
                for point in points:
                    if block_name is not None:
                        msp.add_blockref(block_name, (point.x, point.y), dxfattribs={"layer": layer_name})
                    else:
                        _draw_tree_symbol(msp, point.x, point.y, layer_name)
                    msp.add_text(
                        point.id,
                        dxfattribs={
                            "layer": layer_name,
                            "height": TEXT_HEIGHT_M,
                            "insert": (
                                point.x + TREE_SYMBOL_RADIUS_M + TEXT_HEIGHT_M * 0.3,
                                point.y + TEXT_HEIGHT_M * 0.5,
                            ),
                        },
                    )
                    layer_counts[layer_name] += 1
            else:  # shrub — «пятно-масса» по ГОСТ 21.508-2020, не точка на экземпляр
                for i, cluster in enumerate(_cluster_shrub_points(points), start=1):
                    group_id = f"{layer_name.split('_')[2][0]}-{status_key[0].upper()}{i:03d}"
                    _draw_shrub_mass(msp, cluster, group_id, layer_name)
                    layer_counts[layer_name] += len(cluster)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(output_path))

    return ExportSummary(
        output_path=str(output_path),
        base_dxf_path=str(base_dxf_path),
        layers=[LayerExportSummary(layer_name=name, entity_count=count) for name, count in sorted(layer_counts.items())],
    )
