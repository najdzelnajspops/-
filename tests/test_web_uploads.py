"""Тесты загрузки DWG/DXF через веб-UI (pipeline/web/uploads.py, эндпоинт
POST /api/uploads в pipeline/web/app.py) — прямой запрос пользователя
(2026-09-19): «а если на презентации дадут новый DWG файл?»."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pipeline.web.app import app

client = TestClient(app)

DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "demo_objects" / "peschany_pereulok"
DEMO_FILES = [
    "output_1-5__3_ДЖКХ-25_00752tp.dxf",
    "output_1-5__3_ДЖКХ-25_00752up.dxf",
    "output_1-5__brd.dxf",
]


def _demo_files_payload():
    return [
        ("files", (name, open(DEMO_DIR / name, "rb"), "application/dxf"))
        for name in DEMO_FILES
    ]


def test_upload_valid_dxf_bundle_becomes_usable_object():
    resp = client.post("/api/uploads", files=_demo_files_payload(), data={"label_ru": "Тестовый объект"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["label_ru"] == "Тестовый объект"
    object_id = data["id"]

    # Появился в общем списке объектов наравне с демо-объектами.
    listed = client.get("/api/demo-objects").json()
    assert any(o["id"] == object_id for o in listed)

    # Геометрия строится так же, как для демо-объекта.
    geo = client.get(f"/api/demo-objects/{object_id}/geometry?territory_category=dvorovye")
    assert geo.status_code == 200
    assert geo.json()["site_boundary_status"] == "found"

    # Автогенерация тоже работает на загруженном объекте.
    gen = client.post(f"/api/demo-objects/{object_id}/generate", json={"territory_category": "dvorovye"})
    assert gen.status_code == 200
    assert gen.json()["summary"]["total_points"] > 0


def test_upload_rejects_unsupported_extension(tmp_path):
    bad_file = tmp_path / "not_a_drawing.txt"
    bad_file.write_text("hello", encoding="utf-8")
    with open(bad_file, "rb") as f:
        resp = client.post("/api/uploads", files=[("files", ("not_a_drawing.txt", f, "text/plain"))])
    assert resp.status_code == 400
    assert "неподдерживаемое расширение" in resp.json()["detail"]


def test_upload_rejects_empty_file_list():
    resp = client.post("/api/uploads", files=[])
    assert resp.status_code in (400, 422)
