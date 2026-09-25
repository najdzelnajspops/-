"""Агент ОТК, проверка 6.4 — регрессия на эталонных данных (Service A, real object).

См. docs/AGENTS_PLAN.md, агент №6, проверка 6.4: «сверка с датасетом «Пилотный
проект 20 улиц» ... при каждом изменении логики». В отличие от 6.1-6.3 (которые
работают ВНУТРИ прогона, на любом объекте) 6.4 — это оффлайн-регрессия для
разработчика: полный прогон Service A на одном подтверждённом реальном объекте
(«Песчаный переулок», см. docs/DATA_STRUCTURE.md §11.2 — файлы `output_1-5__*`,
единственная комбинация бандла, дающая подтверждённую площадь границы 50 209 м²)
и проверка, что результат остаётся стабильным.

Пропускается (skip, не fail), если на машине нет ODA File Converter и/или
нераспакованного датасета (23 ГБ, вне git, см. .gitignore) — тот же паттерн,
что и в tests/test_dwg_convert.py.

Два независимых типа проверки:
1. **Повторяемость** (docs/CHECKLIST.md, раздел «Тестирование» — «минимум 5 раз»):
   один и тот же реальный объект, прогнанный ПЯТЬ РАЗ — DXF-результат и
   JSON-отчёт обязаны совпадать (docs/ARCHITECTURE.md §3.1). Это автоматизирует
   пункт чеклиста буквально (не «минимум 2», а ровно требуемые 5) — на реальном
   Ingest (файловая система, парсинг DXF), не только на синтетике.
2. **Эталонные числа** (golden baseline) — площадь границы и число посадок,
   ФАКТИЧЕСКИ измеренные на этом объекте (см. docs/SESSION_LOG.md, 2026-09-17).
   Если сознательно меняете логику density/placement/constraint — эти числа
   изменятся, и это ОЖИДАЕМО: не подгонять тест молча под новое поведение,
   а явно решить, действительно ли новое число правильнее старого, и
   задокументировать это решение в docs/SESSION_LOG.md, прежде чем менять
   константы ниже.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from pipeline.ingest.dwg_convert import convert_dwg_to_dxf, find_oda_converter
from pipeline.run import run_service_a

_OBJECT_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "dataset_extracted"
    / "Датасет"
    / "Пилотный проект 20 улиц"
    / "Пилотный проект 20 улиц"
    / "2. Песчаный переулок"
    / "Генеральный план редформат"
    / "Xrefs"
)
# output_1-5__* — единственная подтверждённая комбинация бандла для этого объекта
# (см. docs/DATA_STRUCTURE.md §11.2: output_1-3__* даёт неверные 66 768 м², другие
# нумерованные варианты — фрагменты одного и того же генплана по частям улицы).
_TP_DWG = _OBJECT_DIR / "output_1-5__3_ДЖКХ-25_00752tp.dwg"
_UP_DWG = _OBJECT_DIR / "output_1-5__3_ДЖКХ-25_00752up.dwg"
_BRD_DWG = _OBJECT_DIR / "output_1-5__brd.dwg"

_DATASET_AVAILABLE = _TP_DWG.is_file() and _UP_DWG.is_file() and _BRD_DWG.is_file()

pytestmark = pytest.mark.skipif(
    find_oda_converter() is None or not _DATASET_AVAILABLE,
    reason="Нужны ODA File Converter и распакованный датасет (23 ГБ, вне git) — см. .gitignore",
)

# Golden baseline, измерено фактически 2026-09-17 (см. docs/SESSION_LOG.md) —
# полный прогон через CLI (python -m pipeline.run) с --territory-category dvorovye.
_EXPECTED_SITE_BOUNDARY_STATUS = "found"
_EXPECTED_SITE_BOUNDARY_AREA_M2 = 50_209  # docs/DATA_STRUCTURE.md §11.2
_EXPECTED_SITE_BOUNDARY_AREA_TOLERANCE_M2 = 50  # округления geometry/polygonize, не логики
_EXPECTED_TREE_PLACED = 4
_EXPECTED_SHRUB_PLACED = 14


def _convert_bundle(out_dir: Path) -> tuple[Path, Path, Path]:
    tp = convert_dwg_to_dxf(_TP_DWG, out_dir)
    up = convert_dwg_to_dxf(_UP_DWG, out_dir)
    brd = convert_dwg_to_dxf(_BRD_DWG, out_dir)
    return tp, up, brd


# Реальная находка 2026-09-17 (первый прогон этого теста на реальном объекте):
# сырой байт-в-байт diff run1.dxf/run2.dxf показал РОВНО 4 различающиеся строки
# на ~8,7 МБ файла — все четыре опознаны как метаданные МОМЕНТА СОХРАНЕНИЯ,
# которые ezdxf проставляет заново при каждом saveas(), а не решение пайплайна
# по геометрии/размещению (аналог того, как любое реальное CAD-ПО штампует файл
# новой датой при пересохранении):
#   - $TDCREATE/$TDUPDATE (юлианская дата создания/последнего изменения);
#   - $VERSIONGUID (новый GUID «версии документа» при каждом saveas());
#   - служебная запись ezdxf в DictionaryVariables «версия библиотеки @ ISO-время».
# Список специально узкий и явный (тот же принцип, что и в
# pipeline/otk/layer_integrity.py::_KNOWN_BENIGN_ROUNDTRIP_DEFAULTS) — маскирует
# ТОЛЬКО эти три конкретных, поимённо опознанных поля, а не любые различия.
_VOLATILE_DXF_METADATA_PATTERNS = [
    (re.compile(r"(\$TD(?:CREATE|UPDATE)\r?\n *40\r?\n)[0-9.]+"), r"\1<SAVE_TIMESTAMP>"),
    (re.compile(r"(\$VERSIONGUID\r?\n *2\r?\n)\{[0-9A-Fa-f-]+\}"), r"\1<VERSION_GUID>"),
    (re.compile(r"\d+\.\d+\.\d+ @ \d{4}-\d{2}-\d{2}T[0-9:.+-]+"), "<EZDXF_SAVE_STAMP>"),
]


def _normalized_dxf_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="strict")
    for pattern, repl in _VOLATILE_DXF_METADATA_PATTERNS:
        text = pattern.sub(repl, text)
    return text


_NUM_REPEAT_RUNS = 5  # docs/CHECKLIST.md: "минимум 5 раз"


def test_real_object_matches_golden_baseline_and_is_reproducible():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tp_dxf, up_dxf, brd_dxf = _convert_bundle(tmp_path / "converted")

        results = []
        for i in range(1, _NUM_REPEAT_RUNS + 1):
            results.append(
                run_service_a(
                    input_dxf_paths=[tp_dxf, up_dxf, brd_dxf],
                    base_dxf_path_for_export=tp_dxf,
                    territory_category="dvorovye",
                    output_dxf_path=tmp_path / f"run{i}.dxf",
                    output_report_path=tmp_path / f"run{i}_report.json",
                )
            )

        # --- 1. Повторяемость: идентичность между всеми N прогонами, кроме заведомо
        # волатильных полей метаданных МОМЕНТА СОХРАНЕНИЯ (см. докстринг у
        # _VOLATILE_DXF_METADATA_PATTERNS выше — узкий, поимённо опознанный список,
        # не общее «игнорировать любые различия»). Всё содержимое чертежа —
        # геометрия, слои, атрибуты сущностей — обязано быть идентичным буквально.
        normalized_dxf_texts = [_normalized_dxf_text(tmp_path / f"run{i}.dxf") for i in range(1, _NUM_REPEAT_RUNS + 1)]
        report_texts = [
            (tmp_path / f"run{i}_report.json").read_text(encoding="utf-8") for i in range(1, _NUM_REPEAT_RUNS + 1)
        ]
        for i in range(1, _NUM_REPEAT_RUNS):
            assert normalized_dxf_texts[0] == normalized_dxf_texts[i], (
                f"DXF-результат прогона {i + 1} не идентичен прогону 1 одного и того же реального "
                "объекта (за вычетом заведомо волатильных полей момента сохранения) — нарушена "
                "детерминированность (docs/ARCHITECTURE.md §3.1)."
            )
            assert report_texts[0] == report_texts[i], (
                f"JSON-отчёт прогона {i + 1} не идентичен прогону 1 одного и того же реального объекта."
            )

        # --- 2. Golden baseline: числа, фактически измеренные на этом объекте ---
        result1 = results[0]
        assert result1.ingest_result.site_boundary_status == _EXPECTED_SITE_BOUNDARY_STATUS
        actual_area = result1.ingest_result.site_boundary.area
        assert abs(actual_area - _EXPECTED_SITE_BOUNDARY_AREA_M2) < _EXPECTED_SITE_BOUNDARY_AREA_TOLERANCE_M2, (
            f"Площадь границы участка {actual_area:.1f} м² отклонилась от эталонной "
            f"{_EXPECTED_SITE_BOUNDARY_AREA_M2} м² больше чем на допуск — либо реальная "
            "регрессия в Ingest/выборе границы, либо намеренное изменение, требующее "
            "осознанного обновления константы (см. докстринг модуля)."
        )
        assert len(result1.placement_results["tree"].placed_points()) == _EXPECTED_TREE_PLACED
        assert len(result1.placement_results["shrub"].placed_points()) == _EXPECTED_SHRUB_PLACED

        # --- 3. Агент ОТК не должен молчать: все проверки обязаны пройти на эталонном объекте ---
        assert result1.boundary_plausibility.verdict == "ok"
        assert result1.layer_integrity.verdict == "ok"
        for kind, check in result1.offset_compliance.items():
            assert check.verdict == "ok", f"{kind}: {check.explanation}"
        report1 = json.loads(report_texts[0])
        assert report1["otk_citation_integrity"]["verdict"] == "ok"


if __name__ == "__main__":
    test_real_object_matches_golden_baseline_and_is_reproducible()
    print("OK: Service A regression on real object (Agent OTK 6.4) passed")
