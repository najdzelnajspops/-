"""CLI точка входа — Placement Review Agent (второй режим работы Service A).

См. pipeline/review/placement_review.py: человек сам решает, что и куда
сажать (список предложенных посадок во входном JSON) — сервис только
проверяет и объясняет, не меняет и не подбирает решение сам. Добавлено
РЯДОМ с автоматической генерацией (pipeline/run.py), не заменяет её —
прямое решение пользователя, 2026-09-18.

Использует те же Ingest + практические политики (существующие насаждения,
красные линии), что и pipeline/run.py, но не строит Constraint Engine
allowed_zone для placement — проверка (pipeline/otk/offset_compliance.py)
и без того независимо пересчитывает расстояния по сырой геометрии.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.catalog.dpioos_species_catalog import TerritoryCategory
from pipeline.constraints.existing_vegetation import ExistingVegetationPolicy
from pipeline.constraints.red_lines_policy import apply_red_lines_policy
from pipeline.ingest.dxf_ingest import ingest_dxf_files
from pipeline.review.placement_review import PlacementReviewReport, ProposedPlanting, review_human_placements


def _load_proposals(path: str | Path) -> list[ProposedPlanting]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        ProposedPlanting(
            id=item["id"],
            species_name_ru=item["species_name_ru"],
            life_form=item["life_form"],
            x=float(item["x"]),
            y=float(item["y"]),
        )
        for item in raw
    ]


def run_placement_review(
    input_dxf_paths: list[str | Path],
    territory_category: TerritoryCategory,
    proposals_path: str | Path,
    output_report_path: str | Path,
) -> PlacementReviewReport:
    ingest_result = ingest_dxf_files(input_dxf_paths)
    if ingest_result.site_boundary is None:
        raise ValueError(
            "Граница участка не найдена во входных файлах (site_boundary_status="
            f"{ingest_result.site_boundary_status!r}) — без неё проверка невозможна. "
            "См. docs/DATA_STRUCTURE.md §11."
        )

    veg_policy = ExistingVegetationPolicy()
    features = veg_policy.apply_to_features(ingest_result.features)
    # Красные линии — та же зона запрета размещения, что и в pipeline/run.py
    # (решение пользователя 2026-09-17, не норма 743-ПП).
    features = apply_red_lines_policy(features)

    proposals = _load_proposals(proposals_path)
    report = review_human_placements(ingest_result.site_boundary, features, territory_category, proposals)

    output = {
        "schema_version": "1.0-placement-review",
        "territory_category": territory_category,
        "source_files": ingest_result.source_files,
        "site_boundary_status": ingest_result.site_boundary_status,
        "verdict": report.verdict,
        "checked_count": report.checked_count,
        "explanation": report.explanation,
        "issues": [
            {"planting_id": issue.planting_id, "kind": issue.kind, "detail": issue.detail}
            for issue in report.issues
        ],
    }
    output_path = Path(output_report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Проект озеленения территории — Placement Review Agent: независимая проверка "
            "ЧЕЛОВЕЧЕСКОГО выбора посадки (второй режим, см. pipeline/review/placement_review.py)."
        )
    )
    parser.add_argument(
        "--input", nargs="+", required=True, help="Путь(и) к DXF-файлам объекта (весь бандл: up/tp/kl/brd)."
    )
    parser.add_argument(
        "--territory-category",
        required=True,
        choices=[
            "dvorovye", "doshkolnye", "obscheobr", "zdravoohr",
            "magistrali", "ploschadi", "parki", "proizvodstvennye",
        ],
        help="Категория территории (ДПиООС, 8 категорий) — параметр запуска, не выводится из DXF.",
    )
    parser.add_argument(
        "--proposals",
        required=True,
        help=(
            "JSON-файл со списком предложенных человеком посадок: "
            '[{"id": "...", "species_name_ru": "...", "life_form": "tree"|"shrub", '
            '"x": ..., "y": ...}, ...] — формат ввода намеренно не привязан к DXF/UI, '
            "см. докстринг pipeline/review/placement_review.py."
        ),
    )
    parser.add_argument("--output-report", required=True, help="Куда сохранить JSON-отчёт проверки.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    report = run_placement_review(
        input_dxf_paths=args.input,
        territory_category=args.territory_category,
        proposals_path=args.proposals,
        output_report_path=args.output_report,
    )

    print(f"Проверено предложений: {report.checked_count}")
    print(f"Вердикт: {report.verdict}")
    if report.verdict != "ok":
        for issue in report.issues:
            location = f"[{issue.planting_id}] " if issue.planting_id else ""
            print(f"  {location}({issue.kind}): {issue.detail}")
    print(f"Отчёт (JSON): {args.output_report}")


if __name__ == "__main__":
    main()
