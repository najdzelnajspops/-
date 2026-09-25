"""Агент ОТК, проверка 6.2 — целостность слоёв (Service A).

См. docs/AGENTS_PLAN.md, агент №6: исходные слои/сущности DXF геометрически
НЕ изменены результатом экспорта — результат размещён ТОЛЬКО в новых слоях
`GREEN_AI_*` (см. pipeline/export/dxf_export.py, LAYER_NAMES).

ПРИНЦИП НЕЗАВИСИМОСТИ (обязателен для агента ОТК, см. AGENTS_PLAN.md): этот
модуль НЕ импортирует и не переиспользует ничего из pipeline/export/dxf_export.py
— открывает оба DXF-файла (исходный и результирующий) заново, самостоятельно,
сравнивая их по сущностям через ezdxf напрямую. Если бы проверка полагалась на
внутренние структуры/допущения генератора экспорта, она не смогла бы поймать
его же баг (например, случайную запись новой точки в существующий слой).

Метод сравнения — по DXF `handle` (уникальный идентификатор сущности внутри
файла): при обычном цикле ezdxf.readfile → добавление новых сущностей →
saveas, handle существующих сущностей не меняется. Сущность считается
изменённой, если её значимые атрибуты (без учёта `handle`/`owner`) отличаются
между исходным и результирующим файлом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf

RESULT_LAYER_PREFIX = "GREEN_AI_"

_IGNORED_ATTRIBS = {"handle", "owner"}

# Атрибуты, которые ezdxf молча опускает при round-trip (readfile -> saveas),
# КОГДА они явно равны значению по умолчанию — это нормализация формата
# библиотекой, не смысловое изменение содержимого сущности. Список специально
# узкий и явный, а не общий механизм «угадывать умолчания ezdxf» — расширять
# только при новой подтверждённой находке, не заранее. Найдено на реальных
# объектах 2026-09-17:
#   - LWPOLYLINE.const_width=0.0 («нет постоянной ширины»);
#   - INSERT.xscale/yscale/zscale=1.0 (блоки-символы — деревья, светофоры,
#     фонари, ЛЭП, крыльца — без масштабирования по умолчанию);
#   - ELLIPSE.extrusion=(0.0, 0.0, 1.0) (стандартный вектор экструзии DXF —
#     «без поворота плоскости», найдено на 2525 сущностях «Олимпийской деревни»);
#   - MTEXT.flow_direction=1 (направление текста «слева направо» — умолчание
#     по спецификации DXF group code 76).
_KNOWN_BENIGN_ROUNDTRIP_DEFAULTS = {
    "const_width": 0.0,
    "xscale": 1.0,
    "yscale": 1.0,
    "zscale": 1.0,
    "extrusion": (0.0, 0.0, 1.0),
    "flow_direction": 1,
}


@dataclass(frozen=True)
class LayerIntegrityIssue:
    kind: str  # "entity_missing" | "entity_modified" | "unexpected_new_entity" | "unexpected_new_layer"
    layer: str
    detail: str


@dataclass
class LayerIntegrityCheck:
    verdict: str  # "ok" | "violation"
    issues: list[LayerIntegrityIssue] = field(default_factory=list)
    base_entity_count: int = 0
    output_entity_count: int = 0
    new_layers_found: list[str] = field(default_factory=list)
    explanation: str = ""


def _normalize_value(value):
    if isinstance(value, str):
        return value
    if hasattr(value, "__iter__"):
        try:
            return tuple(round(float(c), 6) for c in value)
        except (TypeError, ValueError):
            return tuple(value)
    if isinstance(value, float):
        return round(value, 6)
    return value


_MISSING = object()


def _values_equal_allowing_benign_default(key: str, base_value, output_value) -> bool:
    if base_value == output_value:
        return True
    benign_default = _KNOWN_BENIGN_ROUNDTRIP_DEFAULTS.get(key)
    if benign_default is None:
        return False
    # Считаем равными: одна сторона явно хранит значение по умолчанию, другая —
    # атрибут вовсе отсутствует (ezdxf опустил его при перезаписи).
    if base_value is _MISSING and output_value == benign_default:
        return True
    if output_value is _MISSING and base_value == benign_default:
        return True
    return False


def _entity_attribs(entity) -> dict:
    attribs = {}
    for key in entity.dxf.all_existing_dxf_attribs():
        if key in _IGNORED_ATTRIBS:
            continue
        attribs[key] = _normalize_value(entity.dxf.get(key))
    # для LWPOLYLINE значимая геометрия хранится не в dxf-атрибутах, а в точках
    if entity.dxftype() == "LWPOLYLINE":
        attribs["_points"] = tuple(
            tuple(round(c, 6) for c in pt[:2]) for pt in entity.get_points()
        )
    return attribs


def check_layer_integrity(base_dxf_path: str | Path, output_dxf_path: str | Path) -> LayerIntegrityCheck:
    """Независимая проверка: каждая сущность исходного DXF (по handle) присутствует
    в результирующем файле НЕИЗМЕННОЙ; все новые сущности/слои — только GREEN_AI_*."""
    base_doc = ezdxf.readfile(str(base_dxf_path))
    output_doc = ezdxf.readfile(str(output_dxf_path))

    base_layers = {layer.dxf.name for layer in base_doc.layers}
    output_layers = {layer.dxf.name for layer in output_doc.layers}
    new_layers = sorted(output_layers - base_layers)

    issues: list[LayerIntegrityIssue] = []
    for layer_name in new_layers:
        if not layer_name.startswith(RESULT_LAYER_PREFIX):
            issues.append(
                LayerIntegrityIssue(
                    kind="unexpected_new_layer",
                    layer=layer_name,
                    detail=f"Новый слой «{layer_name}» не соответствует ожидаемому префиксу «{RESULT_LAYER_PREFIX}»",
                )
            )

    base_by_handle = {e.dxf.handle: e for e in base_doc.modelspace() if e.dxf.handle}
    output_by_handle = {e.dxf.handle: e for e in output_doc.modelspace() if e.dxf.handle}

    for handle, base_entity in base_by_handle.items():
        layer_name = base_entity.dxf.layer
        output_entity = output_by_handle.get(handle)
        if output_entity is None:
            issues.append(
                LayerIntegrityIssue(
                    kind="entity_missing",
                    layer=layer_name,
                    detail=f"Сущность {base_entity.dxftype()} (handle={handle}) исходного слоя «{layer_name}» отсутствует в результирующем файле",
                )
            )
            continue
        base_attribs = _entity_attribs(base_entity)
        output_attribs = _entity_attribs(output_entity)
        diff_keys = sorted(
            k
            for k in set(base_attribs) | set(output_attribs)
            if not _values_equal_allowing_benign_default(k, base_attribs.get(k, _MISSING), output_attribs.get(k, _MISSING))
        )
        if diff_keys:
            issues.append(
                LayerIntegrityIssue(
                    kind="entity_modified",
                    layer=layer_name,
                    detail=f"Сущность {base_entity.dxftype()} (handle={handle}) изменена: поля {diff_keys}",
                )
            )

    for handle, output_entity in output_by_handle.items():
        if handle not in base_by_handle:
            layer_name = output_entity.dxf.layer
            if not layer_name.startswith(RESULT_LAYER_PREFIX):
                issues.append(
                    LayerIntegrityIssue(
                        kind="unexpected_new_entity",
                        layer=layer_name,
                        detail=f"Новая сущность {output_entity.dxftype()} (handle={handle}) добавлена в слой «{layer_name}», не начинающийся с «{RESULT_LAYER_PREFIX}»",
                    )
                )

    verdict = "ok" if not issues else "violation"
    explanation = (
        "Исходные слои и сущности не изменены; весь новый контент — только в слоях "
        f"«{RESULT_LAYER_PREFIX}*»."
        if verdict == "ok"
        else f"Найдено нарушений целостности: {len(issues)} — см. issues."
    )

    return LayerIntegrityCheck(
        verdict=verdict,
        issues=issues,
        base_entity_count=len(base_by_handle),
        output_entity_count=len(output_by_handle),
        new_layers_found=new_layers,
        explanation=explanation,
    )
