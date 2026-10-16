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


def _uploaded_ids():
    return {o["id"] for o in client.get("/api/demo-objects").json() if o["id"].startswith("upload_")}


def test_upload_broken_dxf_gives_clear_4xx_and_is_not_registered():
    """Найдено 2026-10-16 при проверке в Docker: битый DXF давал 500 Internal Server Error
    без пояснения, а объект оставался в списке. Ожидаем понятную ошибку 422 и чистый список."""
    before = _uploaded_ids()
    resp = client.post("/api/uploads", files=[("files", ("broken.dxf", b"0\nGARBAGE\n", "application/dxf"))])
    assert resp.status_code == 422
    assert "dxf" in resp.json()["detail"].lower()
    assert _uploaded_ids() == before


def test_upload_empty_dxf_gives_clear_4xx_and_is_not_registered():
    before = _uploaded_ids()
    resp = client.post("/api/uploads", files=[("files", ("empty.dxf", b"", "application/dxf"))])
    assert 400 <= resp.status_code < 500
    assert _uploaded_ids() == before


def test_upload_dxf_without_site_boundary_gives_4xx_and_is_not_registered(tmp_path):
    """Корректный DXF без слоя границы участка: это проблема файла пользователя (4xx), не сбой сервера (500)."""
    import ezdxf

    path = tmp_path / "no_boundary.dxf"
    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0), (10, 10))
    doc.saveas(str(path))
    before = _uploaded_ids()
    with open(path, "rb") as f:
        resp = client.post("/api/uploads", files=[("files", ("no_boundary.dxf", f, "application/dxf"))])
    assert resp.status_code == 422
    assert "границ" in resp.json()["detail"].lower()
    assert _uploaded_ids() == before
