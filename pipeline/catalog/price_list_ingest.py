"""Разбор прайс-листа питомника (PDF/Excel/DOCX) и предложение обновлений
для `data/reference/planting_cost_estimates.yaml`.

Прямой запрос пользователя (2026-09-19): «никто так цены обновлять не будет!
нужна загрузка для прайс-листа с распознаванием растений = коррекция списка,
вшитого в сервис». Решено (согласовано с пользователем):
- цены в проекте — по 7 УКРУПНЁННЫМ категориям (хвойное/лиственное ×
  дерево/кустарник × размер, см. `pipeline/catalog/planting_cost.py`), не по
  каждому виду отдельно — точный вид из прайс-листа значения не имеет сам по
  себе, важна только его категория;
- точный матчинг по каталогу ДПиООС (`DpioosSpeciesCatalog.lookup()`) почти
  никогда не сработает на реальных названиях из прайсов питомников (сорта,
  синонимы, отсутствие «(формы и сорта)») — поэтому используется ДВА уровня
  распознавания: (1) точный лукап по каталогу, если повезло; (2) fallback по
  ключевым словам прямо в тексте строки прайса (хвойные роды, «куст»/
  «кустарник», «крупномер»). Ключевые слова — это осознанное, ограниченное
  допущение (не путать с нормативным фактом), поэтому каждая строка, которую
  не удалось классифицировать НИ ОДНИМ способом, помечается `unclassified` и
  всё равно показывается человеку — не отбрасывается молча (сквозной принцип
  проекта — см. `docs/ARCHITECTURE.md` §3.1c, невыдуманная классификация).
- если строка описывает категорию, которой ещё нет в YAML — предлагается
  НОВАЯ категория («сервис довносит неизвестные растения», ответ пользователя),
  а не насильно подгоняется под ближайшую существующую.

Ничего не пишет на диск — это только разбор и построение предложения
(`build_proposal`). Запись (с бэкапом) — в `pipeline/web/app.py`, эндпоинт
`POST /api/price-list/apply`, по прямому решению пользователя «применить сразу
на сервере, с бэкапом» (не «скачать файл», как для нормативных актов —
цены явно помечены в самом YAML как редактируемые человеком, не норма).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
import openpyxl
from docx import Document

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog

LifeForm = Literal["tree", "shrub"]
SizeClass = Literal["standard", "large"]

# Хвойность/жизненная форма по родовому слову выводятся из каталога ДПиООС
# (см. _genus_word_sets ниже) — не из вручную составленного списка. Эти два
# набора слов — единственные ГЕНЕРИЧЕСКИЕ (не привязанные к конкретным родам)
# сигналы, которые действительно нельзя вывести из каталога.
_SHRUB_WORDS = ("куст", "кустарник")
_LARGE_WORDS = ("крупномер",)

_PRICE_LINE_RE = re.compile(
    r"^(?P<name>.{3,}?)[\s\-–—:.]{1,6}(?P<price>\d[\d\s]{2,})\s*(?:руб|₽|р\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedPriceRow:
    name_text: str
    price_rub: float
    source_location: str


@dataclass(frozen=True)
class ClassifiedRow:
    row: ParsedPriceRow
    category_key: str | None  # None -> unclassified
    matched_species_name: str | None
    is_new_category: bool
    new_category_label_ru: str | None = None


@dataclass
class CategoryProposal:
    category_key: str
    label_ru: str
    is_new: bool
    current_default_rub: float | None
    proposed_default_rub: float
    proposed_min_rub: float
    proposed_max_rub: float
    matched_row_count: int
    sample_texts: list[str] = field(default_factory=list)


class UnsupportedPriceListFormatError(ValueError):
    pass


def _parse_number(text: str) -> float:
    return float(text.replace(" ", "").replace("\xa0", ""))


def parse_price_list(path: Path) -> list[ParsedPriceRow]:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return _parse_excel(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    raise UnsupportedPriceListFormatError(
        f"Неподдерживаемый формат прайс-листа: {ext!r} (ожидается .xlsx/.xls/.docx/.pdf)"
    )


def _parse_excel(path: Path) -> list[ParsedPriceRow]:
    rows: list[ParsedPriceRow] = []
    wb = openpyxl.load_workbook(path, data_only=True)
    for sheet in wb.worksheets:
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            name_cell = None
            price_cell = None
            for cell in row:
                if cell is None:
                    continue
                if isinstance(cell, (int, float)) and price_cell is None:
                    price_cell = float(cell)
                elif isinstance(cell, str) and len(cell.strip()) >= 3 and name_cell is None:
                    name_cell = cell.strip()
            if name_cell is not None and price_cell is not None and price_cell > 0:
                rows.append(
                    ParsedPriceRow(
                        name_text=name_cell,
                        price_rub=price_cell,
                        source_location=f"{sheet.title}!строка {row_idx}",
                    )
                )
    return rows


def _parse_docx(path: Path) -> list[ParsedPriceRow]:
    doc = Document(path)
    rows: list[ParsedPriceRow] = []

    for t_idx, table in enumerate(doc.tables, start=1):
        for r_idx, table_row in enumerate(table.rows, start=1):
            cells = [c.text.strip() for c in table_row.cells]
            name_cell = None
            price_cell = None
            for cell_text in cells:
                if not cell_text:
                    continue
                digits = cell_text.replace(" ", "").replace("\xa0", "").replace("руб", "").replace("₽", "")
                if price_cell is None and digits.replace(".", "", 1).isdigit():
                    price_cell = _parse_number(digits)
                elif name_cell is None and len(cell_text) >= 3 and not cell_text.replace(" ", "").isdigit():
                    name_cell = cell_text
            if name_cell is not None and price_cell is not None and price_cell > 0:
                rows.append(
                    ParsedPriceRow(
                        name_text=name_cell,
                        price_rub=price_cell,
                        source_location=f"таблица {t_idx}, строка {r_idx}",
                    )
                )

    if rows:
        return rows

    # Нет таблиц (или ни одной строки не распозналось) — честный текстовый
    # фоллбэк по абзацам, тот же паттерн, что и для PDF без табличного слоя.
    for p_idx, para in enumerate(doc.paragraphs, start=1):
        m = _PRICE_LINE_RE.match(para.text.strip())
        if m:
            rows.append(
                ParsedPriceRow(
                    name_text=m.group("name").strip(),
                    price_rub=_parse_number(m.group("price")),
                    source_location=f"абзац {p_idx}",
                )
            )
    return rows


def _parse_pdf(path: Path) -> list[ParsedPriceRow]:
    rows: list[ParsedPriceRow] = []
    with fitz.open(path) as doc:
        for page_idx, page in enumerate(doc, start=1):
            text = page.get_text()
            for line in text.splitlines():
                m = _PRICE_LINE_RE.match(line.strip())
                if m:
                    rows.append(
                        ParsedPriceRow(
                            name_text=m.group("name").strip(),
                            price_rub=_parse_number(m.group("price")),
                            source_location=f"страница {page_idx}",
                        )
                    )
    return rows


def _genus_word_sets(catalog: DpioosSpeciesCatalog) -> tuple[set[str], set[str], set[str]]:
    """Множества родовых слов (первое слово названия вида), ВЫВЕДЕННЫЕ из
    самого каталога ДПиООС — не придуманы вручную. Нужны как fallback-сигнал
    для строк прайс-листа, которые не находятся точным лукапом (сорта/
    синонимы питомников): например каталог хранит «Липа мелколистная (формы и
    сорта)», а в прайсе может быть просто «Липа мелколистная 30-40» — общее
    родовое слово «липа» позволяет опознать жизненную форму/хвойность, даже
    когда полное название не совпало.

    Реальный баг, найденный на живой проверке (2026-09-20): раньше вместо
    этого использовался жёстко заданный список хвойных родов + проверка
    буквальной подстроки "дерев" в тексте — из-за чего ЛЮБОЕ лиственное
    дерево без слова «дерево» в названии (то есть почти все настоящие
    названия видов) попадало в unclassified. Родовые множества из каталога
    решают это без сочинённого вручную списка пород.
    """
    conifer, deciduous_tree, shrub = set(), set(), set()
    for e in catalog.all_entries():
        genus = e.name_ru.strip().lower().split(" ", 1)[0].strip("(),")
        if not genus:
            continue
        group = e.life_form_group_ru.lower()
        if "хвойн" in group:
            conifer.add(genus)
        elif e.life_form == "tree":
            deciduous_tree.add(genus)
        if e.life_form == "shrub" and "хвойн" not in group:
            shrub.add(genus)
    return conifer, deciduous_tree, shrub


def classify_row(row: ParsedPriceRow, catalog: DpioosSpeciesCatalog) -> ClassifiedRow:
    matches = catalog.lookup(row.name_text)
    if matches:
        entry = matches[0]
        group = entry.life_form_group_ru.lower()
        if "лиан" in group:
            return ClassifiedRow(row, category_key="liana", matched_species_name=entry.name_ru, is_new_category=False)
        is_conifer = "хвойн" in group
        size: SizeClass = "large" if any(w in row.name_text.lower() for w in _LARGE_WORDS) else "standard"
        if entry.life_form == "tree":
            key = f"{'conifer' if is_conifer else 'deciduous'}_tree_{size}"
        else:
            key = "conifer_shrub" if is_conifer else f"deciduous_shrub_{size}"
        return ClassifiedRow(row, category_key=key, matched_species_name=entry.name_ru, is_new_category=False)

    text_lower = row.name_text.lower()
    words = set(text_lower.replace(",", " ").split())
    conifer_genus, deciduous_tree_genus, shrub_genus = _genus_word_sets(catalog)

    is_conifer = bool(words & conifer_genus)
    is_shrub_by_word = any(w in text_lower for w in _SHRUB_WORDS)
    is_shrub_by_genus = bool(words & shrub_genus) and not is_conifer
    is_deciduous_tree_by_genus = bool(words & deciduous_tree_genus)
    is_large = any(w in text_lower for w in _LARGE_WORDS)

    # Явное слово «куст(арник)» в тексте — самый надёжный сигнал, приоритетнее
    # родового совпадения (некоторые роды существуют и в древесной, и в
    # кустарниковой форме — например ива). Ни хвойность, ни древесность/
    # кустарниковость не опознаны ни одним способом — честно "не знаю", а не
    # угадывание наугад.
    is_shrub = is_shrub_by_word or is_shrub_by_genus
    if not is_conifer and not is_shrub and not is_deciduous_tree_by_genus:
        return ClassifiedRow(row, category_key=None, matched_species_name=None, is_new_category=False)

    life_form: LifeForm = "shrub" if is_shrub else "tree"
    size = "large" if is_large else "standard"
    if life_form == "tree":
        key = f"{'conifer' if is_conifer else 'deciduous'}_tree_{size}"
    else:
        key = "conifer_shrub" if is_conifer else f"deciduous_shrub_{size}"
    return ClassifiedRow(row, category_key=key, matched_species_name=None, is_new_category=False)


_KNOWN_CATEGORY_KEYS = {
    "conifer_tree_standard", "conifer_tree_large",
    "deciduous_tree_standard", "deciduous_tree_large",
    "conifer_shrub", "deciduous_shrub_standard", "deciduous_shrub_large",
    "liana",
}

_CATEGORY_LABELS_RU = {
    "conifer_tree_standard": "Хвойное дерево, стандартный размер",
    "conifer_tree_large": "Хвойное дерево, крупномер",
    "deciduous_tree_standard": "Лиственное дерево, стандартный саженец",
    "deciduous_tree_large": "Лиственное дерево, крупномер",
    "conifer_shrub": "Хвойный кустарник",
    "deciduous_shrub_standard": "Лиственный кустарник, стандартный размер",
    "deciduous_shrub_large": "Лиственный кустарник, крупномер",
    "liana": "Лиана",
}


def build_proposal(
    classified_rows: list[ClassifiedRow], current_categories: dict[str, dict]
) -> list[CategoryProposal]:
    """Группирует распознанные строки по категории и предлагает обновление
    (медиана — устойчивее к одной строке-выбросу, чем среднее) — НИЧЕГО не
    применяет, только строит предложение для экрана подтверждения."""
    by_category: dict[str, list[ClassifiedRow]] = {}
    for cr in classified_rows:
        if cr.category_key is None:
            continue
        by_category.setdefault(cr.category_key, []).append(cr)

    proposals: list[CategoryProposal] = []
    for key, rows in by_category.items():
        prices = [cr.row.price_rub for cr in rows]
        is_new = key not in current_categories
        current = current_categories.get(key)
        proposals.append(
            CategoryProposal(
                category_key=key,
                label_ru=_CATEGORY_LABELS_RU.get(key, key),
                is_new=is_new,
                current_default_rub=current["default_rub"] if current else None,
                proposed_default_rub=statistics.median(prices),
                proposed_min_rub=min(prices),
                proposed_max_rub=max(prices),
                matched_row_count=len(rows),
                sample_texts=[cr.row.name_text for cr in rows[:5]],
            )
        )
    return proposals
