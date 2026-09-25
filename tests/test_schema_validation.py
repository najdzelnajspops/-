"""Тесты защитной валидации нормативных справочников при загрузке
(pipeline/common/schema_validation.py) — прямой ответ на запрос пользователя
(2026-09-19): при ручной замене файла в data/reference/ сервис должен падать
понятной ошибкой с указанием файла и поля, а не неконтролируемым KeyError."""

from __future__ import annotations

import json

import pytest

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.catalog.invasive_species import InvasiveSpeciesRegistry
from pipeline.common.schema_validation import DataValidationError, require_keys


def test_require_keys_missing_field(tmp_path):
    bad_file = tmp_path / "broken.json"
    with pytest.raises(DataValidationError, match="species"):
        require_keys({"source_documents": {}}, {"source_documents": dict, "species": list}, source_path=bad_file)


def test_require_keys_wrong_type(tmp_path):
    bad_file = tmp_path / "broken.json"
    with pytest.raises(DataValidationError, match="species"):
        require_keys(
            {"source_documents": {}, "species": "not a list"},
            {"source_documents": dict, "species": list},
            source_path=bad_file,
        )


def test_require_keys_not_a_dict(tmp_path):
    bad_file = tmp_path / "broken.json"
    with pytest.raises(DataValidationError):
        require_keys(["oops"], {}, source_path=bad_file)


def test_require_keys_passes_valid_data(tmp_path):
    bad_file = tmp_path / "ok.json"
    require_keys({"species": []}, {"species": list}, source_path=bad_file)  # не должно бросить


def test_dpioos_catalog_rejects_broken_file(tmp_path):
    broken = tmp_path / "dpioos_species_assortment.json"
    broken.write_text(json.dumps({"species": []}), encoding="utf-8")  # без обязательных полей
    with pytest.raises(DataValidationError, match="source_documents"):
        DpioosSpeciesCatalog(path=broken)


def test_dpioos_catalog_source_updated_at_reflects_file_mtime():
    catalog = DpioosSpeciesCatalog()
    # Формат "YYYY-MM-DD HH:MM UTC" — см. pipeline/common/schema_validation.py
    assert catalog.source_updated_at.endswith(" UTC")
    assert len(catalog.source_updated_at) == len("2026-01-01 00:00 UTC")


def test_invasive_registry_rejects_broken_file(tmp_path):
    broken = tmp_path / "invasive_species.json"
    broken.write_text(json.dumps({"species": []}), encoding="utf-8")  # без source/prohibition_clause
    with pytest.raises(DataValidationError):
        InvasiveSpeciesRegistry(path=broken)
