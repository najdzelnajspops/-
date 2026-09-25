"""Обёртка над ODA File Converter — конвертация DWG в DXF перед Ingest.

См. docs/DATA_STRUCTURE.md §3 (почему это вообще нужно — реальные тестовые
чертежи в датасете почти все в DWG, не в DXF) и docs/ARCHITECTURE.md §5.

ODA File Converter — не pip-пакет, внешний исполняемый файл (см. requirements.txt,
комментарий про системные зависимости). Путь к нему ищем по приоритету:
явный аргумент -> переменная окружения ODA_FILE_CONVERTER_PATH -> типовые пути
на Windows/Linux. Если нигде не нашли — ConverterNotFoundError, а не тихая
попытка обойтись без конвертации.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_DXF_VERSION = "ACAD2018"  # см. docs/OPEN_QUESTIONS.md — версия на усмотрение разработчика

_CANDIDATE_PATHS_WINDOWS = [
    r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe",
]
_CANDIDATE_PATHS_LINUX = [
    "/usr/bin/ODAFileConverter",
    "/opt/ODA/ODAFileConverter",
]


class ConverterNotFoundError(RuntimeError):
    pass


class ConversionFailedError(RuntimeError):
    pass


def find_oda_converter() -> Path | None:
    env_path = os.environ.get("ODA_FILE_CONVERTER_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    for candidate in (*_CANDIDATE_PATHS_WINDOWS, *_CANDIDATE_PATHS_LINUX):
        p = Path(candidate)
        if p.is_file():
            return p

    return None


def convert_dwg_to_dxf(
    dwg_path: str | Path,
    output_dir: str | Path,
    *,
    oda_converter_path: str | Path | None = None,
    dxf_version: str = DEFAULT_DXF_VERSION,
    audit: bool = True,
    timeout_seconds: int = 120,
) -> Path:
    """Конвертирует один DWG-файл в DXF той же версии/аудита, что уже проверено
    вручную на реальных объектах датасета (см. docs/DATA_STRUCTURE.md §9).

    Возвращает путь к результирующему DXF. Не трогает исходный DWG — конвертация
    идёт через отдельные временные input/output директории (ODA File Converter
    принимает на вход и выход только директории, не отдельные файлы).
    """
    dwg_path = Path(dwg_path)
    if not dwg_path.is_file():
        raise FileNotFoundError(f"DWG-файл не найден: {dwg_path}")

    exe = Path(oda_converter_path) if oda_converter_path else find_oda_converter()
    if exe is None or not exe.is_file():
        raise ConverterNotFoundError(
            f"ODA File Converter не найден (путь: {exe!r}). Укажите верный путь явно "
            "(oda_converter_path) или через переменную окружения ODA_FILE_CONVERTER_PATH."
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_in:
        tmp_in_path = Path(tmp_in)
        staged_dwg = tmp_in_path / dwg_path.name
        shutil.copy2(dwg_path, staged_dwg)

        args = [
            str(exe),
            str(tmp_in_path),
            str(output_dir),
            dxf_version,
            "DXF",
            "0",  # recurse
            "1" if audit else "0",
        ]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired as e:
            raise ConversionFailedError(f"ODA File Converter превысил таймаут {timeout_seconds}s") from e

        if proc.returncode != 0:
            raise ConversionFailedError(
                f"ODA File Converter завершился с кодом {proc.returncode}: "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )

    expected_output = output_dir / (dwg_path.stem + ".dxf")
    if not expected_output.is_file():
        raise ConversionFailedError(
            f"Ожидаемый выходной файл не найден после конвертации: {expected_output}"
        )
    return expected_output
