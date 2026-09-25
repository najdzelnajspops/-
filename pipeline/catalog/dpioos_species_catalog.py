"""ДПиООС — каталог основного ассортимента и перспективных видов растений.

Источник: два официальных файла mos.ru (см. docs/NORMATIVE_REFERENCES.md, раздел
«ДПиООС mos.ru — «Основной ассортимент» и «Перспективные виды»»), извлечены в
data/reference/dpioos_species_assortment.json. Принят как ОСНОВНОЙ источник
рекомендаций по видам для категории территории при расхождении с 623-ПП/Таблицей
В.6 (см. pipeline/catalog/species_territory_recommendations.py, которая остаётся
вторичной сверкой) — новее (пост-369-ПП-2026), детальнее (8 категорий вместо 5),
даёт причину ограничения по сноскам, а не голый «+/-».

Жизненная форма (life_form в исходном JSON — русское название группы, например
«Хвойные деревья», «Лиственные кустарники», «Лианы») классифицируется по
подстроке «дерев»/«кустарник» в tree/shrub для нужд Placement Generator (только
эти два вида посадки размечены в 743-ПП/Таблице 3.6.1 отступами) — лианы вне
области MVP, не исключаются из данных, но не участвуют в размещении.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pipeline.common.schema_validation import require_keys, source_updated_at

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "dpioos_species_assortment.json"
)

# Сноска [6] каталога (footnote_legend["6"]): «Не высаживать вблизи плодовых
# культур (яблоня, груша, айва) из-за риска распространения ржавчинных грибов».
# В извлечённых данных сноска [6] стоит только у видов можжевельника — это
# ботанически ожидаемо: ржавчинные грибы рода Gymnosporangium («ржавчина
# можжевельника-яблони») обязательно чередуют хозяев между хвойным
# (можжевельник) и розоцветным (яблоня/груша/айва) в жизненном цикле. Сама
# яблоня/груша/айва сноску [6] не несёт (в каталоге это не отмечено отдельно) —
# определяется по первому слову русского названия (родовое имя всегда первое
# слово в этом каталоге, см. примеры "Яблоня Недзведцкого", "Груша уссурийская").
_RUST_FUNGUS_FOOTNOTE = 6
_RUST_SUSCEPTIBLE_FRUIT_GENERA_RU = ("яблоня", "груша", "айва")

TerritoryCategory = Literal[
    "dvorovye",
    "doshkolnye",
    "obscheobr",
    "zdravoohr",
    "magistrali",
    "ploschadi",
    "parki",
    "proizvodstvennye",
]
LifeForm = Literal["tree", "shrub"]


def _classify_life_form(life_form_group: str | None) -> LifeForm | None:
    if not life_form_group:
        return None
    group = life_form_group.lower()
    if "дерев" in group:
        return "tree"
    if "кустарник" in group:
        return "shrub"
    return None


@dataclass(frozen=True)
class DpioosSpeciesEntry:
    name_ru: str
    life_form: LifeForm
    life_form_group_ru: str
    source: str
    footnotes: tuple[int, ...]
    territory_flags: dict[str, str | None]

    def is_recommended_for(self, category: TerritoryCategory) -> bool:
        return self.territory_flags.get(category) == "+"

    def is_rust_fungus_alternate_host(self) -> bool:
        """Можжевельник и пр. виды со сноской [6] — переносчики ржавчинных грибов
        на плодовые культуры (см. комментарий у _RUST_FUNGUS_FOOTNOTE)."""
        return _RUST_FUNGUS_FOOTNOTE in self.footnotes

    def is_rust_susceptible_fruit_host(self) -> bool:
        """Яблоня/груша/айва — восприимчивый хозяин той же ржавчины (определяется
        по родовому имени, не по сноске — см. комментарий у _RUST_FUNGUS_FOOTNOTE)."""
        first_word = self.name_ru.strip().lower().split(" ", 1)[0]
        return first_word in _RUST_SUSCEPTIBLE_FRUIT_GENERA_RU


def species_conflict_reason(a: DpioosSpeciesEntry, b: DpioosSpeciesEntry) -> str | None:
    """Возвращает текст причины конфликта, если пара видов a/b не должна
    размещаться в непосредственной близости друг от друга, иначе None.

    Единственное правило MVP (сноска [6] каталога ДПиООС): можжевельник рядом
    с яблоней/грушей/айвой — переносчик и восприимчивый хозяин одного и того же
    ржавчинного гриба. Симметрично — не важно, кто из пары "дерево", кто "куст".
    """
    if a.is_rust_fungus_alternate_host() and b.is_rust_susceptible_fruit_host():
        return f'[6] «{a.name_ru}» — переносчик ржавчинных грибов, конфликтует с «{b.name_ru}»'
    if b.is_rust_fungus_alternate_host() and a.is_rust_susceptible_fruit_host():
        return f'[6] «{b.name_ru}» — переносчик ржавчинных грибов, конфликтует с «{a.name_ru}»'
    return None


class DpioosSpeciesCatalog:
    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        require_keys(
            raw,
            {"source_documents": dict, "territory_categories": dict, "footnote_legend": dict, "species": list},
            source_path=path,
        )

        self.source_documents: dict[str, str] = raw["source_documents"]
        self.territory_categories: dict[str, str] = raw["territory_categories"]
        self.footnote_legend: dict[str, str] = raw["footnote_legend"]
        self.column_quality_note: str = raw.get("column_quality_note", "")

        self._entries: list[DpioosSpeciesEntry] = []
        for item in raw["species"]:
            lf = _classify_life_form(item.get("life_form"))
            if lf is None:
                continue
            self._entries.append(
                DpioosSpeciesEntry(
                    name_ru=item["name"],
                    life_form=lf,
                    life_form_group_ru=item["life_form"],
                    source=item["source"],
                    footnotes=tuple(item.get("footnotes", [])),
                    territory_flags=item.get("territory_flags", {}),
                )
            )

    @property
    def source_updated_at(self) -> str:
        return source_updated_at(self._path)

    def all_entries(self) -> list[DpioosSpeciesEntry]:
        return list(self._entries)

    def lookup(self, name_ru: str) -> list[DpioosSpeciesEntry]:
        """Может вернуть больше одной записи — один и тот же вид иногда встречается
        и в «основном ассортименте», и в «перспективных видах» с разными сносками."""
        norm = name_ru.strip().lower()
        return [e for e in self._entries if e.name_ru.strip().lower() == norm]

    def recommended_for(self, category: TerritoryCategory, life_form: LifeForm) -> list[DpioosSpeciesEntry]:
        return [e for e in self._entries if e.life_form == life_form and e.is_recommended_for(category)]

    def citation_for(self, entry: DpioosSpeciesEntry, category: TerritoryCategory) -> str:
        """Текст обоснования для Interpretation Report / DXF-атрибута — строится
        только из уже проверенных полей самой записи (см. AGENTS_PLAN.md, проверка
        6.3 агента ОТК — свободный текст LLM никогда не подставляется как
        обоснование)."""
        doc_name = self.source_documents.get(entry.source, entry.source)
        category_label = self.territory_categories.get(category, category)
        base = f'{doc_name} — категория «{category_label}»: вид «{entry.name_ru}» рекомендован (+)'
        if entry.footnotes:
            notes = "; ".join(
                f"[{n}] {self.footnote_legend.get(str(n), '')}" for n in entry.footnotes
            )
            base += f". Примечания источника: {notes}"
        return base
