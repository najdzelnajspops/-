"""Ручное указание класса напряжения ЛЭП оператором (2026-09-25, прямой запрос
пользователя): «на схеме это не указано, но человек имеет дополнительные
материалы на руках и может самостоятельно определить класс объекта — сервис
должен пересчитать параметры участка».

По умолчанию (см. pipeline/ingest/dxf_ingest.py) ЛЭП, чей класс напряжения не
удаётся определить по имени слоя чертежа, получает САМЫЙ ШИРОКИЙ консервативный
отступ (55 м, тариф 1150 кВ) — честный отказ от угадывания, но на практике
способен занять почти весь участок (см. docs/OPEN_QUESTIONS.md, случай
«Камчатская улица» — 0.06% допустимой зоны). Если оператор ЗНАЕТ реальный класс
напряжения из документов, не отражённых в чертеже, — этот модуль пересчитывает
отступ по этому классу вместо худшего случая.

Это НЕ отменяет консервативную политику по умолчанию — override применяется
только когда оператор явно его задал; без него поведение не меняется.
"""

from __future__ import annotations

from dataclasses import replace

from pipeline.constraints.buffer_engine import ConstraintFeature
from pipeline.constraints.lep_zones import LepZoneRegistry

UNKNOWN_VOLTAGE_BOUNDARY_TYPE = "power_line_overhead_unknown_voltage"


def apply_lep_voltage_override(
    features: list[ConstraintFeature],
    voltage_kv: float | None,
    registry: LepZoneRegistry,
) -> list[ConstraintFeature]:
    """voltage_kv=None — поведение не меняется (features возвращаются как есть).
    Иначе — у всех ЛЭП с неопределённым по чертежу классом напряжения отступ
    пересчитывается по указанному классу. citation задан (в отличие от
    исходного консервативного допущения, где он намеренно None) — оператор
    сообщил конкретный факт, а не сервис угадал; это явно проговорено в самой
    цитате, чтобы в отчёте было видно, что источник — не чертёж."""
    if voltage_kv is None:
        return features

    width_m = registry.overhead_line_zone_width_m(voltage_kv)
    citation = (
        f"{registry.citation()} — класс напряжения ({voltage_kv:g} кВ) указан "
        "оператором вручную по дополнительным материалам объекта, не определён "
        "по данным чертежа"
    )
    return [
        replace(f, explicit_distance_m=width_m, explicit_citation=citation)
        if f.boundary_type == UNKNOWN_VOLTAGE_BOUNDARY_TYPE
        else f
        for f in features
    ]
