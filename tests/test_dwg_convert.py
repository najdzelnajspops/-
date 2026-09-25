"""Тест обёртки над ODA File Converter. Пропускается (skip, не fail), если
конвертер не установлен на текущей машине — это внешняя системная зависимость,
не pip-пакет (см. requirements.txt), её отсутствие не должно ронять весь набор
тестов на машине, где её ещё не поставили."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf
import pytest

from pipeline.ingest.dwg_convert import ConverterNotFoundError, convert_dwg_to_dxf, find_oda_converter
from pipeline.ingest.dxf_ingest import ingest_dxf_files

pytestmark = pytest.mark.skipif(
    find_oda_converter() is None,
    reason="ODA File Converter не найден на этой машине (не pip-зависимость, ставится отдельно)",
)


def _build_minimal_dwg(path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    doc.layers.new(name="obj|Водопровод")
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "obj|Водопровод"})
    # ezdxf не пишет DWG напрямую — сохраняем DXF и подсовываем конвертеру для
    # проверки самого механизма вызова; на реальных объектах вход — настоящий DWG.
    doc.saveas(str(path))


def test_oda_converter_is_found_on_this_machine():
    assert find_oda_converter() is not None


_SAMPLE_OBJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "dataset_extracted"
    / "Датасет"
    / "Пилотный проект 20 улиц"
    / "Пилотный проект 20 улиц"
    / "1. Олимпийская деревня"
    / "Исходные данные"
    / "10000176_Генплан_Олимп - Standard"
    / "3ДЖКХ-25_03117"
)
# ВАЖНО (см. docs/DATA_STRUCTURE.md §9, обновление после разбора этого объекта):
# главный файл "_06_10000176_Генплан_Олимп.dwg" ссылается на инженерные сети через
# XREF — без резолва внешних ссылок ezdxf видит только ACAD_PROXY_OBJECT, реальной
# геометрии там нет. Настоящие данные — в ОТДЕЛЬНЫХ компонентных DWG того же
# eTransmit-бандла: "...up.dwg" (подземные коммуникации) и "...tp.dwg" (топоплан,
# включая границу площадки/здания/ЛЭП/деревья) — их и нужно конвертировать и
# передавать вместе в ingest_dxf_files().
_SAMPLE_UP_DWG = _SAMPLE_OBJECT_DIR / "output[1-18]_3_ДЖКХ-25_03117up.dwg"
_SAMPLE_TP_DWG = _SAMPLE_OBJECT_DIR / "output[1-18]_3_ДЖКХ-25_03117tp.dwg"


@pytest.mark.skipif(
    not (_SAMPLE_UP_DWG.is_file() and _SAMPLE_TP_DWG.is_file()),
    reason="Реальный датасет не распакован на этой машине (23 ГБ, вне git — см. .gitignore)",
)
def test_convert_dwg_to_dxf_end_to_end_on_real_object():
    """Конвертация + ingest реального бандла (не главного «Генплана» — см.
    комментарий у _SAMPLE_UP_DWG выше). Числа ниже — фактически измеренные на
    этом объекте 2026-09-15, не ожидания «на глаз»: 140447 объектов-ограничений,
    11 разных boundary_type, граница участка находится, но с низкой уверенностью
    (реальные данные рисуют «Граница площадки» разрозненными отрезками — честно
    отражено статусом found_low_confidence, а не молча принимается как надёжная)."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "out"
        up_dxf = convert_dwg_to_dxf(_SAMPLE_UP_DWG, out_dir)
        tp_dxf = convert_dwg_to_dxf(_SAMPLE_TP_DWG, out_dir)

        result = ingest_dxf_files([up_dxf, tp_dxf])

        boundary_types = {f.boundary_type for f in result.features}
        assert len(boundary_types) >= 5  # реально найдено 11, порог занижен намеренно (устойчивость к мелким правкам словаря алиасов)
        assert len(result.features) > 10000  # реально 140447 — порог грубый, не хрупкий к незначащим изменениям
        assert result.site_boundary_status in ("found", "found_low_confidence")
        assert "Подземные коммуникации" in result.unclassified_layers  # осознанно не классифицируемый общий слой, см. layer_name_aliases.yaml


def test_converter_not_found_raises_explicit_error():
    # Путь передан НАПРЯМУЮ (не через find_oda_converter/переменную окружения),
    # чтобы тест был валиден и на машине, где конвертер реально установлен —
    # иначе find_oda_converter() нашёл бы настоящий и замаскировал проверку.
    with tempfile.TemporaryDirectory() as tmp:
        dummy_dwg = Path(tmp) / "dummy.dwg"
        dummy_dwg.write_bytes(b"not a real dwg")
        with pytest.raises(ConverterNotFoundError):
            convert_dwg_to_dxf(
                dummy_dwg, Path(tmp) / "out", oda_converter_path="/nonexistent/path/to/converter"
            )


if __name__ == "__main__":
    test_oda_converter_is_found_on_this_machine()
    test_converter_not_found_raises_explicit_error()
    if _SAMPLE_UP_DWG.is_file() and _SAMPLE_TP_DWG.is_file():
        test_convert_dwg_to_dxf_end_to_end_on_real_object()
    print("OK: DWG convert tests passed")
