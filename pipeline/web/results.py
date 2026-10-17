"""Файлы результата последних генераций веб-интерфейса (2026-10-17): итоговый DXF и
отчёт для пользователя, чтобы их можно было скачать и открыть из окна сервиса.

Хранятся в системном временном каталоге, в памяти процесса — только последние
MAX_RESULTS результатов (DXF крупного объекта весит десятки мегабайт, диск не должен
расти без границ). После перезапуска процесса старые ссылки перестают работать —
это осознанное упрощение для демонстрации: нужный результат всегда можно получить
повторной генерацией.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

MAX_RESULTS = 3

DXF_NAME = "result.dxf"
REPORT_DIR_NAME = "human"
REPORT_HTML_NAME = "human_report.html"
# Единственные файлы отчёта, которые отдаёт сервис (белый список вместо произвольного пути).
REPORT_FILES = {
    REPORT_HTML_NAME: "text/html; charset=utf-8",
    "scheme_before.png": "image/png",
    "scheme_after.png": "image/png",
}


@dataclass
class ResultEntry:
    result_id: str
    directory: Path
    demo_id: str

    @property
    def dxf_path(self) -> Path:
        return self.directory / DXF_NAME

    @property
    def report_dir(self) -> Path:
        return self.directory / REPORT_DIR_NAME


_RESULTS: "OrderedDict[str, ResultEntry]" = OrderedDict()


def new_result_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="zelyonyj_kontur_result_"))


def register_result(directory: Path, demo_id: str) -> str:
    """Принимает каталог результата во владение и возвращает идентификатор.
    Самые старые результаты сверх лимита удаляются вместе с каталогами."""
    result_id = uuid.uuid4().hex[:16]
    _RESULTS[result_id] = ResultEntry(result_id=result_id, directory=Path(directory), demo_id=demo_id)
    while len(_RESULTS) > MAX_RESULTS:
        oldest = next(iter(_RESULTS))
        forget_result(oldest)
    return result_id


def get_result(result_id: str) -> ResultEntry | None:
    return _RESULTS.get(result_id)


def forget_result(result_id: str) -> None:
    entry = _RESULTS.pop(result_id, None)
    if entry is not None:
        shutil.rmtree(entry.directory, ignore_errors=True)
