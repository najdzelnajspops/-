"""Тесты веб-UI API (pipeline/web/app.py) — второй способ запуска сервиса,
2026-09-18. Использует реальный демо-объект (data/demo_objects/peschany_pereulok/,
закоммичен в git — не сырой датасет), не моки — те же выводы, что и у CLI
(pipeline/run.py, pipeline/review_run.py) на этом же объекте."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl
from fastapi.testclient import TestClient

import pipeline.web.app as app_module
import pipeline.web.audit_log as audit_log
from pipeline.web.app import app

client = TestClient(app)


def test_list_demo_objects():
    resp = client.get("/api/demo-objects")
    assert resp.status_code == 200
    data = resp.json()
    assert any(o["id"] == "peschany_pereulok" for o in data)


def test_territory_categories():
    resp = client.get("/api/territory-categories")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert "dvorovye" in ids
    assert len(ids) == 8


def test_geometry_returns_valid_site_boundary():
    resp = client.get("/api/demo-objects/peschany_pereulok/geometry?territory_category=dvorovye")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site_boundary_status"] == "found"
    assert data["bounds"] is not None
    assert len(data["site_boundary"]) >= 1
    assert "tree" in data["allowed_zone"]
    assert "shrub" in data["allowed_zone"]


def test_geometry_unknown_demo_object_404():
    resp = client.get("/api/demo-objects/does-not-exist/geometry")
    assert resp.status_code == 404


def test_species_endpoint_returns_recommended_species():
    resp = client.get("/api/species?territory_category=dvorovye&life_form=tree")
    assert resp.status_code == 200
    species = resp.json()
    assert len(species) > 0
    assert all("name_ru" in s and "citation" in s for s in species)
    # Отсортировано по алфавиту — тот же порядок, что и в pick_species()
    # автогенератора (см. pipeline/placement/generator.py) — не случайный.
    names = [s["name_ru"] for s in species]
    assert names == sorted(names)


def test_generate_produces_placements_and_summary():
    resp = client.post("/api/demo-objects/peschany_pereulok/generate", json={"territory_category": "dvorovye"})
    assert resp.status_code == 200
    report = resp.json()
    assert report["summary"]["total_points"] > 0
    assert report["summary"]["placed"] > 0
    assert "lep_unknown_voltage" in report
    assert len(report["placements"]) == report["summary"]["total_points"]


def test_generate_with_selected_species_distributes_them_across_points():
    """Чек-боксы видов на вкладке автогенерации (2026-09-24) — прямой запрос
    пользователя: несколько выбранных видов дерева должны реально
    использоваться на плане, не только первый по алфавиту."""
    species_resp = client.get("/api/species?territory_category=dvorovye&life_form=tree")
    all_tree_names = [s["name_ru"] for s in species_resp.json()]
    selected = all_tree_names[:3]

    resp = client.post(
        "/api/demo-objects/peschany_pereulok/generate",
        json={"territory_category": "dvorovye", "selected_species": {"tree": selected}},
    )
    assert resp.status_code == 200
    report = resp.json()

    tree_placements = [p for p in report["placements"] if p["planting_kind"] == "tree" and p["status"] == "placed"]
    used_names = {p["species_name_ru"] for p in tree_placements}
    assert used_names == set(selected)  # все три реально использованы, не только первый

    tree_choice = next(sc for sc in report["species_choices"] if sc["planting_kind"] == "tree")
    assert set(tree_choice["chosen_species_names_ru"]) == set(selected)


def test_generate_without_selected_species_key_is_backward_compatible():
    """Без selected_species (или без tree/shrub внутри) — поведение НЕ должно
    отличаться от того, что было до чек-боксов (одвидовой режим по умолчанию)."""
    resp = client.post(
        "/api/demo-objects/peschany_pereulok/generate",
        json={"territory_category": "dvorovye", "selected_species": {}},
    )
    assert resp.status_code == 200
    report = resp.json()
    tree_placements = [p for p in report["placements"] if p["planting_kind"] == "tree" and p["status"] == "placed"]
    assert len({p["species_name_ru"] for p in tree_placements}) == 1  # ровно один вид, как раньше


def test_review_flags_unknown_species():
    payload = {
        "territory_category": "dvorovye",
        "proposals": [
            {
                "id": "p1",
                "species_name_ru": "Совершенно Вымышленное Растение",
                "life_form": "tree",
                "x": 615.9855,
                "y": 14258.7075,
            }
        ],
    }
    resp = client.post("/api/demo-objects/peschany_pereulok/review", json=payload)
    assert resp.status_code == 200
    result = resp.json()
    assert result["verdict"] == "issues_found"
    assert any(i["kind"] == "unknown_species" for i in result["issues"])
    # Прямой запрос пользователя (2026-09-20): подсчёт стоимости при ручном
    # вводе — вид не найден в каталоге, поэтому честно исключён из суммы (0),
    # не додуман, но при этом посчитан (не None, т.к. предложения были).
    assert result["total_estimated_cost_rub"] == 0
    assert result["cost_unknown_count"] == 1


def test_review_no_proposals_is_ok():
    resp = client.post(
        "/api/demo-objects/peschany_pereulok/review",
        json={"territory_category": "dvorovye", "proposals": []},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["verdict"] == "ok"
    assert result["checked_count"] == 0
    assert result["total_estimated_cost_rub"] is None


def test_review_estimates_cost_for_known_species():
    species = client.get("/api/species?territory_category=dvorovye&life_form=tree").json()
    assert species, "Нет рекомендованных деревьев для dvorovye в реальном каталоге — тест не может продолжить"
    payload = {
        "territory_category": "dvorovye",
        "proposals": [
            {"id": "p1", "species_name_ru": species[0]["name_ru"], "life_form": "tree", "x": 700.0, "y": 14230.0}
        ],
    }
    resp = client.post("/api/demo-objects/peschany_pereulok/review", json=payload)
    assert resp.status_code == 200
    result = resp.json()
    assert result["total_estimated_cost_rub"] > 0
    assert result["cost_unknown_count"] == 0


def test_data_sources_endpoint_lists_all_registries():
    # Прямой ответ на запрос пользователя (2026-09-19) об обновлении
    # нормативной базы — показывает дату файла на диске для каждого
    # справочника, чтобы было видно, какая версия реально используется.
    resp = client.get("/api/data-sources")
    assert resp.status_code == 200
    sources = resp.json()
    assert len(sources) == 9
    for s in sources:
        assert s["file"].endswith((".json", ".yaml"))
        assert s["updated_at"].endswith(" UTC")


def test_index_page_served():
    """Заголовок страницы/логотип — «Зелёный контур» (2026-09-24, ребрендинг
    по макету пользователя) вместо прежнего служебного «Проект озеленения
    территории»."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Зелёный контур" in resp.text


def test_generate_writes_audit_log(tmp_path, monkeypatch):
    # Прямой запрос пользователя (2026-09-19): полный ответ сервиса должен
    # сохраняться на сервере независимо от того, что потом скроет пользователь
    # в браузере/при печати — см. pipeline/web/audit_log.py.
    monkeypatch.setattr(audit_log, "AUDIT_DIR", tmp_path)
    resp = client.post("/api/demo-objects/peschany_pereulok/generate", json={"territory_category": "dvorovye"})
    assert resp.status_code == 200

    log_files = list(tmp_path.glob("*.jsonl"))
    assert len(log_files) == 1
    lines = log_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "generate"
    assert record["demo_id"] == "peschany_pereulok"
    assert record["territory_category"] == "dvorovye"
    assert record["response"]["summary"]["total_points"] > 0


def test_review_writes_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "AUDIT_DIR", tmp_path)
    payload = {"territory_category": "dvorovye", "proposals": []}
    resp = client.post("/api/demo-objects/peschany_pereulok/review", json=payload)
    assert resp.status_code == 200

    log_files = list(tmp_path.glob("*.jsonl"))
    assert len(log_files) == 1
    record = json.loads(log_files[0].read_text(encoding="utf-8").strip())
    assert record["kind"] == "review"
    assert record["response"]["verdict"] == "ok"


def _make_price_list_excel(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ель обыкновенная, крупномер", 30000])
    ws.append(["Ель колючая, крупномер", 35000])
    ws.append(["Совершенно новая категория растений XYZ", 777])
    wb.save(path)


def test_price_list_preview_returns_proposals(tmp_path):
    price_list_path = tmp_path / "price.xlsx"
    _make_price_list_excel(price_list_path)

    with open(price_list_path, "rb") as f:
        resp = client.post(
            "/api/price-list/preview",
            files={"file": ("price.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows_found"] == 3
    assert data["unclassified_count"] >= 1  # "Совершенно новая категория..." не распознаётся по ключевым словам
    conifer_large = next(p for p in data["proposals"] if p["category_key"] == "conifer_tree_large")
    assert conifer_large["matched_row_count"] == 2
    assert conifer_large["is_new"] is False


def test_price_list_apply_updates_file_with_backup(tmp_path, monkeypatch):
    real_path = app_module.PLANTING_COST_PATH
    working_copy = tmp_path / "planting_cost_estimates.yaml"
    working_copy.write_bytes(real_path.read_bytes())
    monkeypatch.setattr(app_module, "PLANTING_COST_PATH", working_copy)

    payload = {
        "updates": [
            {
                "category_key": "conifer_tree_large",
                "label_ru": "Хвойное дерево, крупномер",
                "default_rub": 32500,
                "min_rub": 30000,
                "max_rub": 35000,
            }
        ]
    }
    resp = client.post("/api/price-list/apply", json=payload)
    assert resp.status_code == 200
    result = resp.json()
    assert result["updated_categories"] == 1

    backups_dir = working_copy.parent / "backups"
    backups = list(backups_dir.glob(f"{working_copy.stem}_*.yaml"))
    assert len(backups) == 1

    import yaml as yaml_module

    with open(working_copy, encoding="utf-8") as f:
        updated = yaml_module.safe_load(f)
    assert updated["categories"]["conifer_tree_large"]["default_rub"] == 32500

    # Второй apply в тот же день не должен плодить бэкапы сверх лимита (2).
    resp2 = client.post("/api/price-list/apply", json=payload)
    assert resp2.status_code == 200
    backups_after = list(backups_dir.glob(f"{working_copy.stem}_*.yaml"))
    assert len(backups_after) <= 2


def test_price_list_apply_rejects_empty_updates():
    resp = client.post("/api/price-list/apply", json={"updates": []})
    assert resp.status_code == 400


if __name__ == "__main__":
    test_list_demo_objects()
    test_territory_categories()
    test_geometry_returns_valid_site_boundary()
    test_geometry_unknown_demo_object_404()
    test_species_endpoint_returns_recommended_species()
    test_generate_produces_placements_and_summary()
    test_review_flags_unknown_species()
    test_review_no_proposals_is_ok()
    test_index_page_served()
    print("OK: web app tests passed")
