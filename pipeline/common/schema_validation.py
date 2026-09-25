"""Защитная валидация верхнего уровня нормативных справочников при загрузке.

Прямой запрос пользователя (2026-09-19): справочники в data/reference/
(каталог видов, отступы 743-ПП, густота 623-ПП, инвазивные виды 369-ПП и т.д.)
должны обновляться вручную — заменой файла на диске (каждый реестр
перечитывает файл заново на каждое создание объекта, без кэширования — см.
pipeline/web/app.py, где реестры создаются заново на каждый HTTP-запрос,
поэтому замена подхватывается сразу, без перезапуска сервиса). Сервис должен
защищаться от ошибок при такой замене (переименован ключ, подсунут не тот
файл, битый JSON/YAML) — понятной ошибкой с указанием файла и поля, а не
неконтролируемым `KeyError`/`TypeError` без объяснения причины.

Проверяется только структура ВЕРХНЕГО уровня (какие поля обязаны быть и
какого типа) — не полная глубокая схема: это защита от «подменили не тот
файл», а не замена ручной сверки нового содержимого с первоисточником акта
(см. docs/DATA_UPDATE_GUIDE.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def source_updated_at(path: Path) -> str:
    """Дата последнего изменения файла справочника на диске — практическая
    замена полю "version"/"updated_at" внутри самих данных (которого там нет
    и вводить не нужно): при ручной замене файла эта дата обновится сама,
    без риска, что кто-то забудет проставить версию вручную. Используется
    в GET /api/data-sources (pipeline/web/app.py), чтобы было видно, какая
    редакция справочника сейчас реально используется."""
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class DataValidationError(ValueError):
    """Справочник в data/reference/ повреждён или имеет неожиданную структуру."""


def require_keys(raw: object, required: dict[str, type], *, source_path: Path) -> None:
    if not isinstance(raw, dict):
        raise DataValidationError(
            f"Файл {source_path} повреждён или имеет неверную структуру: ожидался "
            f"объект JSON/YAML верхнего уровня (словарь), получено {type(raw).__name__}."
        )
    for key, expected_type in required.items():
        if key not in raw:
            raise DataValidationError(
                f"Файл {source_path} повреждён или имеет неверную структуру: "
                f"отсутствует обязательное поле «{key}»."
            )
        if not isinstance(raw[key], expected_type):
            raise DataValidationError(
                f"Файл {source_path} повреждён или имеет неверную структуру: поле "
                f"«{key}» должно быть типа {expected_type.__name__}, "
                f"получено {type(raw[key]).__name__}."
            )
