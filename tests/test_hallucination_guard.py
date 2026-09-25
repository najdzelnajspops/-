"""Целевой тест на «галлюцинацию» — см. docs/CHECKLIST.md: «намеренно подать
кейс с неполными/противоречивыми данными и убедиться, что агент ОТК
блокирует/помечает результат, а не пропускает уверенный, но необоснованный
вывод».

В отличие от юнит-тестов отдельных проверок агента ОТК (tests/test_boundary_
plausibility.py, tests/test_layer_integrity.py, tests/test_citation_integrity.py,
tests/test_offset_compliance.py — каждый дёргает свою функцию напрямую с
искусственными данными), этот файл гоняет ПОЛНЫЙ пайплайн (`run_service_a()`)
на заведомо противоречивом входе — граница участка декларирует ничтожно малую
площадь, но реальные объекты-коммуникации на том же чертеже физически
простираются далеко за её пределы (реальный сценарий такого сбоя — баг сборки
границы из разрозненных отрезков, см. docs/DATA_STRUCTURE.md §11.3, ровно тот
случай, ради которого проверка 6.1a и была написана). Цель — доказать, что
на таком входе система:
  (а) не падает и не зависает;
  (б) явно и честно возвращает `implausible`, а не тихо принимает границу на
      веру и не выдаёт уверенный план посадки как ни в чём не бывало;
  (в) вердикт `implausible` реально доходит до JSON-отчёта (не теряется между
      Ingest и Interpretation Report Agent) — то, что видит жюри/оператор.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from pipeline.run import run_service_a


def _build_contradictory_dxf(path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()

    for layer_name in ["obj|Граница площадки", "obj|Водопровод"]:
        doc.layers.new(name=layer_name)

    # Граница участка декларирует смехотворно малую площадь (2х2 м).
    msp.add_lwpolyline(
        [(0, 0), (2, 0), (2, 2), (0, 2)],
        close=True,
        dxfattribs={"layer": "obj|Граница площадки"},
    )
    # Но реальный водопровод на том же чертеже тянется на 2 километра —
    # заведомое противоречие: участок якобы 4 м², а инфраструктура вокруг
    # физически покрывает ~4 км².
    msp.add_line((0, 0), (2000, 0), dxfattribs={"layer": "obj|Водопровод"})
    msp.add_line((2000, 0), (2000, 2000), dxfattribs={"layer": "obj|Водопровод"})

    doc.saveas(str(path))


def test_contradictory_boundary_is_flagged_not_silently_trusted():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "contradictory.dxf"
        output_dxf = Path(tmp) / "output.dxf"
        output_report = Path(tmp) / "report.json"
        _build_contradictory_dxf(dxf_path)

        # (а) не падает и не зависает — даже на заведомо противоречивом входе.
        result = run_service_a(
            input_dxf_paths=[dxf_path],
            base_dxf_path_for_export=dxf_path,
            territory_category="dvorovye",
            output_dxf_path=output_dxf,
            output_report_path=output_report,
        )

        # (б) явный, не подавленный вердикт implausible — а не молчаливое
        # доверие декларированной границе.
        assert result.boundary_plausibility is not None
        assert result.boundary_plausibility.verdict == "implausible"
        assert result.boundary_plausibility.ratio is not None
        assert result.boundary_plausibility.ratio < 0.01
        assert result.boundary_plausibility.explanation  # не пустая строка-заглушка

        # (в) вердикт реально доходит до JSON-отчёта, не теряется по пути.
        assert result.report is not None
        otk_boundary = result.report["otk_boundary_plausibility"]
        assert otk_boundary is not None
        assert otk_boundary["verdict"] == "implausible"
        assert otk_boundary["explanation"]

        # Система не выдаёт этот заведомо сомнительный результат как «просто ещё
        # один обычный маленький участок» — report.json физически существует и
        # читаем (для дальнейшей ручной проверки оператором), но implausible-
        # вердикт в нём присутствует БЕЗ подавления, независимо от того, сколько
        # точек посадки (если вообще хоть одна) уместилось в эти 4 м².
        assert output_report.exists()


if __name__ == "__main__":
    test_contradictory_boundary_is_flagged_not_silently_trusted()
    print("OK")
