"""Загрузка произвольного DWG/DXF через веб-UI — прямой ответ на вопрос
пользователя (2026-09-19): «а если на презентации дадут новый DWG файл?».

Отличие от `pipeline/web/demo_objects.py` (заранее подготовленные, закоммиченные
объекты): здесь объекты появляются во время работы процесса, живут в памяти
(`_UPLOADS`) и во временном каталоге на диске — сознательное упрощение для
живой презентации, не для промышленной эксплуатации:
- каталоги создаются через `tempfile.mkdtemp()` (БЕЗ context manager) — объект
  должен пережить несколько последующих запросов (geometry/generate/review) в
  рамках одной демонстрации, а не исчезать сразу после первого ответа, как
  временный каталог в `api_generate`. Каталоги накапливаются до перезапуска
  процесса — приемлемо для сессии показа, не для сервера, который никогда не
  перезапускается (в таком случае нужна бы явная уборка по TTL).
- реестр `_UPLOADS` живёт только в памяти процесса — перезапуск сервиса теряет
  загруженные объекты (это ожидаемо: они не должны переживать деплой, в
  отличие от `data/demo_objects/`, которые закоммичены в git).

Конвертация DWG→DXF переиспользует существующий `pipeline/ingest/dwg_convert.py`
(тот же путь, что и CLI) — ошибки (нет ODA File Converter, битый DWG) не
глотаются, пробрасываются вызывающему коду как понятные исключения.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from pipeline.ingest.dwg_convert import convert_dwg_to_dxf

ALLOWED_EXTENSIONS = {".dwg", ".dxf"}
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # 200 МБ — защита от повторения реального
# инцидента проекта (баг линейности отчёта раздул временный файл до 168 ГБ и
# уронил диск, см. docs/SESSION_LOG.md) — на входе ограничиваем заведомо раньше.


class UploadValidationError(ValueError):
    """Файл не прошёл базовую проверку (расширение/размер) — вина пользователя,
    не сервиса, поэтому это отдельный тип ошибки от ConverterNotFoundError/
    ConversionFailedError (вина окружения) и от ошибок ingest (данные не те)."""


@dataclass(frozen=True)
class UploadedObject:
    id: str
    label_ru: str
    input_paths: tuple[Path, ...]
    base_dxf_path: Path
    default_territory_category: str


_UPLOADS: dict[str, UploadedObject] = {}


def list_uploaded_objects() -> list[UploadedObject]:
    return list(_UPLOADS.values())


def get_uploaded_object(object_id: str) -> UploadedObject | None:
    return _UPLOADS.get(object_id)


def _validate(filename: str, size: int) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            f"Файл «{filename}»: неподдерживаемое расширение «{ext}» — принимаются только .dwg и .dxf."
        )
    if size > MAX_FILE_SIZE_BYTES:
        raise UploadValidationError(
            f"Файл «{filename}»: размер {size / 1024 / 1024:.1f} МБ превышает лимит "
            f"{MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f} МБ."
        )


def register_upload(files: list[UploadFile], label_ru: str | None = None) -> UploadedObject:
    """Сохраняет загруженные файлы, конвертирует DWG→DXF при необходимости,
    регистрирует объект в памяти процесса. Бросает `UploadValidationError`
    (расширение/размер), `ConverterNotFoundError`/`ConversionFailedError`
    (см. pipeline/ingest/dwg_convert.py) — вызывающий код (pipeline/web/app.py)
    отвечает за перевод их в понятный HTTP-ответ."""
    if not files:
        raise UploadValidationError("Не выбрано ни одного файла.")

    contents: list[tuple[str, bytes]] = []
    for f in files:
        raw = f.file.read()
        _validate(f.filename or "unnamed", len(raw))
        contents.append((f.filename, raw))

    work_dir = Path(tempfile.mkdtemp(prefix="upload_"))
    dxf_paths: list[Path] = []
    for filename, raw in contents:
        staged = work_dir / filename
        staged.write_bytes(raw)
        if staged.suffix.lower() == ".dwg":
            dxf_paths.append(convert_dwg_to_dxf(staged, work_dir))
        else:
            dxf_paths.append(staged)

    object_id = f"upload_{uuid.uuid4().hex[:12]}"
    obj = UploadedObject(
        id=object_id,
        label_ru=label_ru or f"Загруженный объект ({dxf_paths[0].stem})",
        input_paths=tuple(dxf_paths),
        base_dxf_path=dxf_paths[0],
        default_territory_category="dvorovye",
    )
    _UPLOADS[object_id] = obj
    return obj
