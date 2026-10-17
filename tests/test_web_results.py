"""Файлы результата генерации в веб-интерфейсе (2026-10-17): скачивание итогового DXF
и открытие отчёта для пользователя из окна сервиса. Реальный демо-объект, не моки."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import pipeline.web.audit_log as audit_log
import pipeline.web.results as results
from pipeline.web.app import app

client = TestClient(app)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    audit_log.AUDIT_DIR = tmp_path_factory.mktemp("audit")
    resp = client.post("/api/demo-objects/peschany_pereulok/generate", json={"territory_category": "dvorovye"})
    assert resp.status_code == 200
    return resp.json()


def test_generate_response_carries_result_id(generated):
    assert generated["result_id"]
    assert generated["placements"]  # прежнее содержимое ответа не потеряно


def test_dxf_download_has_new_layers_and_filename(generated):
    resp = client.get(f"/api/results/{generated['result_id']}/dxf")
    assert resp.status_code == 200
    assert "peschany_pereulok" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.dxf"')
    body = resp.content
    assert b"SECTION" in body[:200]
    assert b"GREEN_AI_TREE_PROPOSED" in body


def test_report_opens_in_browser_with_schemes(generated):
    rid = generated["result_id"]
    page = client.get(f"/api/results/{rid}/report/human_report.html")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Отчёт по итогам озеленения" in page.text
    assert "window.print()" in page.text
    # схемы подгружаются относительными ссылками из отчёта — они должны открываться
    for name in ("scheme_before.png", "scheme_after.png"):
        assert name in page.text
        img = client.get(f"/api/results/{rid}/report/{name}")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/png"


def test_report_page_does_not_second_guess_the_user(generated):
    page = client.get(f"/api/results/{generated['result_id']}/report/human_report.html").text
    for word in ("человек", "оператор", "ЧЕЛОВЕЧЕСК", "при сомнении"):
        assert word not in page


def test_unknown_result_is_404():
    assert client.get("/api/results/deadbeef/dxf").status_code == 404
    assert client.get("/api/results/deadbeef/report/human_report.html").status_code == 404


def test_report_serves_only_known_files(generated):
    rid = generated["result_id"]
    assert client.get(f"/api/results/{rid}/report/result.dxf").status_code == 404
    assert client.get(f"/api/results/{rid}/report/..%2Fresult.dxf").status_code == 404


def test_old_results_are_evicted(tmp_path, monkeypatch):
    """Диск не растёт без границ: хранятся только последние несколько результатов."""
    monkeypatch.setattr(results, "MAX_RESULTS", 2)
    ids = []
    for i in range(3):
        d = tmp_path / f"r{i}"
        d.mkdir()
        (d / "result.dxf").write_text("0\nSECTION\n", encoding="utf-8")
        ids.append(results.register_result(d, "obj"))
    assert results.get_result(ids[0]) is None
    assert not (tmp_path / "r0").exists()
    assert results.get_result(ids[1]) is not None
    assert results.get_result(ids[2]) is not None
    for i in ids:
        results.forget_result(i)


def test_report_with_user_voltage_has_no_conflicting_warning(tmp_path):
    """Если пользователь указал класс напряжения, отчёт не пишет «применён максимально
    консервативный отступ 55 м» — отступ посчитан по его выбору."""
    resp = client.post(
        "/api/demo-objects/peschany_pereulok/generate",
        json={"territory_category": "dvorovye", "lep_voltage_kv": 10},
    )
    rid = resp.json()["result_id"]
    page = client.get(f"/api/results/{rid}/report/human_report.html").text
    assert "указан пользователем: 10 кВ" in page
    assert "Применён максимально консервативный отступ" not in page
    assert 'class="warn"' not in page
