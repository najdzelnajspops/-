"""Тесты DXF Ingest Agent на СИНТЕТИЧЕСКОМ DXF (не зависят от 23 ГБ реального
датасета — он не входит в git, см. .gitignore). Слои названы по реальной
конвенции, подтверждённой на живом объекте (docs/DATA_STRUCTURE.md §9)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from pipeline.ingest.dxf_ingest import ingest_dxf, ingest_dxf_files
from pipeline.ingest.layer_classifier import LayerClassifier


def _build_synthetic_dxf(path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()

    for layer_name in [
        "obj|Водопровод",
        "obj|Газопровод",
        "obj|Здания",
        "obj|Граница площадки",
        "obj|ЛЭП",
        "obj|НеизвестныйСлойКоммуникации",
    ]:
        doc.layers.new(name=layer_name)

    # Граница участка — квадрат 0..100
    msp.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "obj|Граница площадки"},
    )
    # Здание — прямоугольник внутри участка
    msp.add_lwpolyline(
        [(60, 60), (90, 60), (90, 90), (60, 90)],
        close=True,
        dxfattribs={"layer": "obj|Здания"},
    )
    # Водопровод — линия
    msp.add_line((10, 0), (10, 100), dxfattribs={"layer": "obj|Водопровод"})
    # Газопровод — линия
    msp.add_line((30, 0), (30, 100), dxfattribs={"layer": "obj|Газопровод"})
    # ЛЭП — линия, класс напряжения по слою не определить
    msp.add_line((50, 0), (50, 100), dxfattribs={"layer": "obj|ЛЭП"})
    # Неклассифицированный слой — должен попасть в unclassified, не пропасть молча
    msp.add_line((70, 0), (70, 20), dxfattribs={"layer": "obj|НеизвестныйСлойКоммуникации"})
    # Неподдерживаемый тип сущности — должен попасть в skipped_entity_types
    msp.add_text("тестовая подпись", dxfattribs={"layer": "obj|Здания"})

    doc.saveas(str(path))


def test_dxf_ingest_classifies_real_layer_naming_convention():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "synthetic.dxf"
        _build_synthetic_dxf(dxf_path)

        result = ingest_dxf(dxf_path)

        assert result.site_boundary_status == "found"
        assert result.site_boundary is not None
        assert result.site_boundary.area == 100 * 100

        boundary_types = {f.boundary_type for f in result.features}
        assert "water_supply" in boundary_types
        assert "gas_pipeline" in boundary_types
        assert "building_wall" in boundary_types

        # ЛЭП без известного класса напряжения — не пропущена молча, а помечена
        # для ручного уточнения, с консервативным (не проверенным) отступом.
        assert len(result.lep_features_needing_voltage) == 1
        lep_feature = next(f for f in result.features if f.id == result.lep_features_needing_voltage[0])
        assert lep_feature.boundary_type == "power_line_overhead_unknown_voltage"
        assert lep_feature.explicit_citation is None  # явно НЕ выдаётся за проверенную норму

        # Неизвестный слой коммуникации — зафиксирован, не потерян.
        assert "НеизвестныйСлойКоммуникации" in result.unclassified_layers
        assert result.unclassified_layers["НеизвестныйСлойКоммуникации"].entity_count == 1

        # TEXT — не геометрия ограничения, но должен быть виден как пропущенный тип,
        # а не бесследно исчезнуть.
        assert "TEXT" in result.skipped_entity_types


def test_dxf_ingest_is_deterministic_across_independent_calls():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "synthetic.dxf"
        _build_synthetic_dxf(dxf_path)

        result_a = ingest_dxf(dxf_path)
        result_b = ingest_dxf(dxf_path)

        ids_a = [f.id for f in result_a.features]
        ids_b = [f.id for f in result_b.features]
        assert ids_a == ids_b  # порядок объектов не должен "плавать" между вызовами

        types_a = [f.boundary_type for f in result_a.features]
        types_b = [f.boundary_type for f in result_b.features]
        assert types_a == types_b


def test_site_boundary_resolves_insert_block_on_boundary_layer():
    """Реальный случай (docs/DATA_STRUCTURE.md §11.1, объект «Куликовская улица»):
    единственная сущность на слое границы участка — INSERT (блок-ссылка), не
    линии/полилиния напрямую. Граница обязана резолвиться через геометрию блока
    (virtual_entities()), а не сводиться к одной точке вставки."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "insert_boundary.dxf"

        doc = ezdxf.new("R2018")
        block = doc.blocks.new(name="BORDER_BLOCK")
        block.add_lwpolyline([(0, 0), (40, 0), (40, 25), (0, 25)], close=True)

        doc.layers.new(name="obj|Граница заказа")
        msp = doc.modelspace()
        msp.add_blockref(
            "BORDER_BLOCK", insert=(100, 200), dxfattribs={"layer": "obj|Граница заказа"}
        )

        doc.saveas(str(dxf_path))

        result = ingest_dxf(dxf_path)

        assert result.site_boundary_status == "found"
        assert result.site_boundary is not None
        assert result.site_boundary.area == 40 * 25
        # Блок вставлен со смещением (100, 200) — граница должна быть в мировых
        # координатах после трансформации INSERT, а не в локальных координатах блока.
        minx, miny, maxx, maxy = result.site_boundary.bounds
        assert (minx, miny, maxx, maxy) == (100.0, 200.0, 140.0, 225.0)


def test_site_boundary_prefers_reliable_geometry_over_higher_ranked_layer_name():
    """Реальный случай (docs/DATA_STRUCTURE.md §11.2 — «Олимпийская деревня» и
    «Грузинская М ул» при объединении полного бандла up+tp+brd): более
    приоритетный по имени слой («Граница площадки», ранг 0) даёт МУСОРНЫЙ
    мелкий полигон из разрозненных отрезков (низкая confidence), а менее
    приоритетный слой («Граница заказа», ранг 2) — надёжный явный замкнутый
    контур (confidence=1.0). Правильный выбор — надёжная геометрия, а не более
    "приоритетное" по имени, но мусорное значение."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "boundary_priority.dxf"

        doc = ezdxf.new("R2018")
        for layer in ("obj|Граница площадки", "obj|Граница заказа"):
            doc.layers.new(name=layer)
        msp = doc.modelspace()

        # "Граница площадки" (ранг 0): разрозненные отрезки, охватывающие большую
        # область (0,0)-(1000,1000), но замыкающие только маленький локальный кусок
        # в углу — типичный сбой polygonize() на грязных реальных данных.
        msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "obj|Граница площадки"})
        msp.add_line((1000, 0), (1000, 1000), dxfattribs={"layer": "obj|Граница площадки"})
        msp.add_line((1000, 1000), (0, 1000), dxfattribs={"layer": "obj|Граница площадки"})
        # Не замыкаем этот большой контур — оставляем разрыв, зато рядом даём
        # маленький полностью замкнутый мусорный треугольник.
        msp.add_line((0, 0), (5, 0), dxfattribs={"layer": "obj|Граница площадки"})
        msp.add_line((5, 0), (0, 5), dxfattribs={"layer": "obj|Граница площадки"})
        msp.add_line((0, 5), (0, 0), dxfattribs={"layer": "obj|Граница площадки"})

        # "Граница заказа" (ранг 2, "хуже" по приоритету имени): чистый явный
        # замкнутый контур 100x100 — надёжная геометрия.
        reliable = msp.add_lwpolyline(
            [(200, 200), (300, 200), (300, 300), (200, 300)],
            dxfattribs={"layer": "obj|Граница заказа"},
        )
        reliable.closed = True

        doc.saveas(str(dxf_path))

        result = ingest_dxf(dxf_path)

        assert result.site_boundary_status == "found"
        assert result.site_boundary is not None
        assert result.site_boundary.area == 100 * 100  # надёжный контур, не мусорный треугольник


def test_layer_classifier_matches_real_convention_examples():
    """Примеры взяты из реально прочитанного слоя объекта «Олимпийская деревня»
    (docs/DATA_STRUCTURE.md §9), не выдуманы для теста."""
    classifier = LayerClassifier()

    c1 = classifier.classify("output[1-18]_3_ДЖКХ-25_03117up|Водопровод")
    assert c1.status == "mapped"
    assert c1.boundary_type == "water_supply"

    c2 = classifier.classify("output[1-18]_3_ДЖКХ-25_03117tp|Отдельно стоящее дерево")
    assert c2.status == "mapped"
    assert c2.boundary_type == "existing_tree_preserve"

    c3 = classifier.classify("output[1-18]_brd|Рамки")
    assert c3.status == "ignored"

    c4 = classifier.classify("_ГП_Колодцы Ремонт ГАЗ")  # реальный слой без "|", вне словаря
    assert c4.status == "unclassified"


def test_ingest_dxf_files_cache_hit_returns_equal_but_independent_result():
    """Регрессионный тест на кеш 2026-09-28 (прямой запрос пользователя —
    веб-сервис на VPS «оч долго грузится» один и тот же демо-объект при каждом
    запросе): ingest_dxf_files() — чистая функция от содержимого входных
    файлов, повторный вызов с теми же путями обязан вернуть результат с тем же
    содержимым, но НЕЗАВИСИМЫЙ объект (мутация одного результата не должна
    портить кеш для следующего вызова)."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "synthetic.dxf"
        _build_synthetic_dxf(dxf_path)

        first = ingest_dxf_files([dxf_path])
        second = ingest_dxf_files([dxf_path])

        assert first is not second
        assert len(first.features) == len(second.features) == len(first.features)
        assert first.site_boundary.equals(second.site_boundary)
        assert first.unclassified_layers.keys() == second.unclassified_layers.keys()

        # Мутация результата первого вызова не должна повлиять на третий вызов
        # (доказывает, что из кеша каждый раз отдаётся защитная копия, а не
        # общий изменяемый объект).
        first.skipped_entity_types.add("ПОДДЕЛЬНЫЙ_ТИП_ДЛЯ_ТЕСТА")
        first.features.append(first.features[0])

        third = ingest_dxf_files([dxf_path])
        assert "ПОДДЕЛЬНЫЙ_ТИП_ДЛЯ_ТЕСТА" not in third.skipped_entity_types
        assert len(third.features) == len(second.features)


def test_ingest_dxf_files_cache_invalidates_when_file_changes_on_disk():
    """Если файл демо-объекта реально заменили на диске (другое содержимое,
    другой mtime) — кеш обязан не отдать старый результат молча."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "synthetic.dxf"
        _build_synthetic_dxf(dxf_path)
        first = ingest_dxf_files([dxf_path])
        first_feature_count = len(first.features)

        # Полностью новый DXF с другим числом объектов, тот же путь.
        doc = ezdxf.new("R2018")
        doc.layers.new(name="obj|Граница площадки")
        doc.modelspace().add_lwpolyline(
            [(0, 0), (50, 0), (50, 50), (0, 50)], close=True, dxfattribs={"layer": "obj|Граница площадки"}
        )
        doc.saveas(str(dxf_path))

        second = ingest_dxf_files([dxf_path])
        assert len(second.features) != first_feature_count
        assert second.site_boundary.area == 50 * 50


if __name__ == "__main__":
    test_dxf_ingest_classifies_real_layer_naming_convention()
    test_dxf_ingest_is_deterministic_across_independent_calls()
    test_site_boundary_resolves_insert_block_on_boundary_layer()
    test_site_boundary_prefers_reliable_geometry_over_higher_ranked_layer_name()
    test_layer_classifier_matches_real_convention_examples()
    test_ingest_dxf_files_cache_hit_returns_equal_but_independent_result()
    test_ingest_dxf_files_cache_invalidates_when_file_changes_on_disk()
    print("OK: DXF Ingest tests passed")
