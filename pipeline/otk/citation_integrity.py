"""Агент ОТК, проверка 6.3 — анти-галлюцинационная сверка обоснований (Service A).

См. docs/AGENTS_PLAN.md, агент №6, проверка 6.3: «каждая ссылка на норму
резолвится в верифицированную запись [[NORMATIVE_REFERENCES]]; если текст
объяснения где-либо формируется генеративной моделью — он не идёт в отчёт
напрямую, а собирается по шаблону из уже проверенных полей (норма/пункт/
дистанция/вердикт), свободный текст LLM никогда не подставляется как
обоснование».

В ЭТОМ КОДЕ свободный текст LLM никогда не участвует в сборке цитат (см.
`DpioosSpeciesCatalog.citation_for()`, `OffsetRegistry.citation()` — обе строят
текст только из полей уже верифицированных источников, YAML/JSON, вручную
сверенных с первоисточником, см. docs/NORMATIVE_REFERENCES.md). Поэтому цель
ЭТОЙ проверки — не «поймать LLM за галлюцинацией» (её здесь структурно нет), а
поймать РЕГРЕССИЮ: ручную правку текста цитаты в обход этих функций, drift
между кодом и данными реестра (реестр обновили — код с ним разъехался), или
цитату не того объекта, случайно скопированную при правке кода.

ПРИНЦИП НЕЗАВИСИМОСТИ (обязателен для агента ОТК, см. AGENTS_PLAN.md): проверяет
уже готовый JSON-отчёт (финальный артефакт, который увидит пользователь) — не
переиспользует ConstraintMapResult/PlacementResult изнутри пайплайна. Пересчитывает
ожидаемую цитату заново через СВЕЖИЙ экземпляр соответствующего реестра
(OffsetRegistry/DpioosSpeciesCatalog/LepZoneRegistry — источники данных, не
решения генератора) и сравнивает строку-в-строку с тем, что реально попало
в отчёт.

ИЗВЕСТНОЕ ОГРАНИЧЕНИЕ: свободный пояснительный текст вне поля `citation`
(`density_note`, `species_conflict_note`, `explanation` у самих проверок ОТК) —
собирается по f-строке в pipeline/run.py/generator.py из уже проверенных чисел,
но не как отдельная ссылка на конкретный пункт акта — вне области этой проверки
(это инженерные пояснения в смысле AGENTS_PLAN.md, не «ссылка на норму» 6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.catalog.dpioos_species_catalog import DpioosSpeciesCatalog
from pipeline.constraints.lep_zones import LepZoneRegistry
from pipeline.constraints.offset_registry import OffsetRegistry
from pipeline.placement.generator import GRID_SPACING_CITATION

UNVERIFIED_PLACEHOLDER = "[требует верификации]"


@dataclass(frozen=True)
class CitationViolation:
    location: str
    kind: str  # "unverified_row_has_substantive_text" | "unrecognized_citation" |
    # "species_citation_mismatch" | "species_citation_without_species" | "grid_spacing_citation_mismatch"
    detail: str


@dataclass
class CitationIntegrityCheck:
    verdict: str  # "ok" | "violation"
    violations: list[CitationViolation] = field(default_factory=list)
    citations_checked: int = 0
    explanation: str = ""


def check_citation_integrity(report: dict) -> CitationIntegrityCheck:
    """Независимая сверка каждой ссылки на норму в готовом JSON-отчёте: строка
    цитаты пересчитывается заново из соответствующего верифицированного реестра
    и сравнивается буквально с тем, что попало в отчёт."""
    registry = OffsetRegistry()
    species_catalog = DpioosSpeciesCatalog()
    lep_citation = LepZoneRegistry().citation()

    violations: list[CitationViolation] = []
    checked = 0

    applied_norms = report.get("applied_constraint_norms_by_kind") or {}
    for kind, rows in applied_norms.items():
        for row in rows:
            checked += 1
            boundary_type = row.get("boundary_type")
            citation = row.get("citation")
            location = f"applied_constraint_norms_by_kind.{kind}[feature_id={row.get('feature_id')}]"

            if not row.get("verified"):
                if citation != UNVERIFIED_PLACEHOLDER:
                    violations.append(
                        CitationViolation(
                            location=location,
                            kind="unverified_row_has_substantive_text",
                            detail=(
                                f"Строка помечена verified=False, но citation={citation!r} — "
                                f"ожидался честный плейсхолдер {UNVERIFIED_PLACEHOLDER!r}, а не "
                                "подставленный текст (это и есть требование анти-галлюцинационной "
                                "проверки: непроверенное не должно выглядеть как проверенное)."
                            ),
                        )
                    )
                continue

            expected_offset_citation = registry.citation(boundary_type)
            if citation == expected_offset_citation or citation == lep_citation:
                continue
            violations.append(
                CitationViolation(
                    location=location,
                    kind="unrecognized_citation",
                    detail=(
                        f"citation={citation!r} для boundary_type={boundary_type!r} не совпадает ни с "
                        f"OffsetRegistry.citation() ({expected_offset_citation!r}), ни с "
                        f"LepZoneRegistry.citation() ({lep_citation!r}) — источник цитаты не "
                        "прослеживается до верифицированного реестра."
                    ),
                )
            )

    for point in report.get("placements") or []:
        point_id = point.get("id")

        checked += 1
        grid_citation = point.get("grid_spacing_citation")
        if grid_citation != GRID_SPACING_CITATION:
            violations.append(
                CitationViolation(
                    location=f"placements[id={point_id}].grid_spacing_citation",
                    kind="grid_spacing_citation_mismatch",
                    detail=(
                        f"grid_spacing_citation={grid_citation!r} не совпадает с verified-константой "
                        f"{GRID_SPACING_CITATION!r}."
                    ),
                )
            )

        species_name = point.get("species_name_ru")
        species_citation = point.get("species_citation")
        checked += 1
        if species_name is None:
            if species_citation is not None:
                violations.append(
                    CitationViolation(
                        location=f"placements[id={point_id}].species_citation",
                        kind="species_citation_without_species",
                        detail=(
                            f"species_name_ru отсутствует, но species_citation={species_citation!r} "
                            "задан — обоснование без вида, которому оно принадлежит."
                        ),
                    )
                )
            continue

        category = point.get("territory_category")
        planting_kind = point.get("planting_kind")
        candidates = [
            e
            for e in species_catalog.lookup(species_name)
            if e.life_form == planting_kind and e.is_recommended_for(category)
        ]
        expected_citations = {species_catalog.citation_for(e, category) for e in candidates}
        if species_citation not in expected_citations:
            violations.append(
                CitationViolation(
                    location=f"placements[id={point_id}].species_citation",
                    kind="species_citation_mismatch",
                    detail=(
                        f"species_citation={species_citation!r} для вида «{species_name}» "
                        f"({planting_kind}, категория {category!r}) не совпадает ни с одним "
                        f"вариантом, пересчитанным заново из DpioosSpeciesCatalog "
                        f"({sorted(expected_citations)!r})."
                    ),
                )
            )

    verdict = "ok" if not violations else "violation"
    explanation = (
        f"Независимая проверка {checked} ссылок на нормы: каждая цитата в отчёте пересчитана заново "
        "из верифицированного реестра-источника и совпадает буквально."
        if verdict == "ok"
        else f"Найдено нарушений целостности цитат: {len(violations)} — см. violations."
    )
    return CitationIntegrityCheck(
        verdict=verdict, violations=violations, citations_checked=checked, explanation=explanation
    )
