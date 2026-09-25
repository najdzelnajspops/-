"""Красные линии — зона, запрещённая для размещения насаждений.

НЕ норма конкретного НПА (743-ПП про красные линии ничего не говорит — это
самостоятельное градостроительное ограничение, юридическая граница между
территорией участка и улично-дорожной сетью) — прямое решение пользователя
(владельца проекта), 2026-09-17: «красные линии - зона запрета размещения
насаждений». Поэтому этот модуль (как и pipeline/constraints/existing_vegetation.py)
всегда возвращает verified=False вместе с числом — вызывающий код обязан
прокидывать эту пометку дальше в отчёт (см. pipeline/constraints/buffer_engine.py:
`verified = explicit_citation is not None`), а не выдавать её за подтверждённую
норму 743-ПП. Агент ОТК 6.3 (pipeline/otk/citation_integrity.py) независимо
проверяет именно это: строка с verified=False обязана нести честный плейсхолдер,
а не текст, похожий на цитату акта.

Технически — не «отступ N метров от линии», а «сама нарисованная площадь
запрещена»: explicit_distance_m=0.0 на уже полигональной геометрии (buffer(0)
на Polygon — no-op, см. pipeline/constraints/buffer_engine.py, BUFFER_QUAD_SEGS)
запрещает РОВНО нарисованную площадь, а не добавляет полосу вокруг неё. Реальная
геометрия слоя «Красные линии» на разных объектах датасета устроена по-разному
(см. docs/DATA_STRUCTURE.md) — где-то это HATCH-заливки (см.
pipeline/ingest/dxf_ingest.py, `_hatch_to_geometry`), где-то — точечные
INSERT-маркеры без содержательной площади (честное ограничение данных, не баг).
"""

from __future__ import annotations

import dataclasses

from pipeline.constraints.buffer_engine import ConstraintFeature

RED_LINES_BOUNDARY_TYPE = "red_lines_no_planting"

RED_LINES_LABEL_RU = "Красная линия (зона запрета размещения насаждений — решение проекта, не норма 743-ПП)"


def apply_red_lines_policy(features: list[ConstraintFeature]) -> list[ConstraintFeature]:
    """Красные линии (boundary_type == red_lines_no_planting) — вся нарисованная
    площадь запрещена для посадки любого вида (дерево и кустарник одинаково).
    explicit_citation сознательно НЕ устанавливается (см. docstring модуля).
    Остальные объекты возвращаются без изменений."""
    result: list[ConstraintFeature] = []
    for feature in features:
        if feature.boundary_type != RED_LINES_BOUNDARY_TYPE:
            result.append(feature)
            continue
        result.append(
            dataclasses.replace(
                feature,
                explicit_distance_m=0.0,
                explicit_citation=None,
                label=feature.label or RED_LINES_LABEL_RU,
            )
        )
    return result
