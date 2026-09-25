"""Человекочитаемый отчёт (Markdown + HTML) — см. docs/REPORT_DESIGN.md.

Прямой ответ на разрыв, найденный при анализе требований ТЗ (2026-09-23):
JSON-отчёт (pipeline/report/interpretation_report.py) полный и правильный, но
без CAD и умения читать сырой JSON эксперт ДПиООС/жюри физически не может
увидеть результат. Этот модуль — ЧИСТЫЙ РЕНДЕРЕР уже посчитанных структур
(тот же JSON-отчёт + геометрии Constraint Engine), не пересчитывает и не
сочиняет ничего заново — тот же принцип «один источник истины, несколько
потребителей», что и в DXF Export Agent (см. docs/REPORT_DESIGN.md).

Формат — Markdown + HTML (решение пользователя, 2026-09-23). PDF сознательно
НЕ реализован в этом проходе: JSON и Markdown/HTML уже удовлетворяют букве ТЗ
п.3.3 («JSON/CSV/Markdown/PDF на выбор команды»), а PDF потребовал бы новой
тяжёлой зависимости (weasyprint/headless-браузер) с реальным риском для
воспроизводимости Docker-образа — см. docs/LIMITATIONS.md.

Схема "до/после" рисуется matplotlib поверх ConstraintMapResult.site_boundary/
allowed_zone/forbidden_zone — тех же геометрий, что уже посчитаны Constraint
Engine, не новый пересчёт по сырым ConstraintFeature (упрощение: коммуникации
не различаются по типу отдельным цветом на схеме — только итоговая допустимая/
запрещённая зона; это уже отвечает на главный вопрос отчёта «где можно
сажать», см. docs/LIMITATIONS.md про этот выбор).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless — без этого matplotlib на Linux-сервере без дисплея падает
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader
from shapely.geometry.base import BaseGeometry

from pipeline.constraints.buffer_engine import ConstraintMapResult
from pipeline.placement.generator import PlacementPoint, PlacementResult

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"

_KIND_LABEL_RU = {"tree": "деревья", "shrub": "кустарники"}


@dataclass
class HumanReportPaths:
    markdown_path: Path
    html_path: Path
    scheme_before_path: Path
    scheme_after_path: Path


def _plot_geometry(ax, geom: BaseGeometry, **kwargs) -> None:
    """Рисует один shapely-полигон/мультиполигон на matplotlib-оси — единственная
    геометрия, которую реально нужно уметь рисовать здесь (site_boundary/
    allowed_zone/forbidden_zone уже посчитаны как Polygon/MultiPolygon)."""
    if geom.is_empty:
        return
    polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polygons:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, **kwargs)


def _render_scheme_image(
    constraint_results: dict[str, ConstraintMapResult],
    placements: dict[str, list[PlacementPoint]] | None,
    title: str,
    output_path: Path,
) -> None:
    """Рисует схему участка — по одной панели на вид посадки (дерево/кустарник),
    т.к. допустимая зона у них разная (разные нормы отступа). `placements=None`
    — схема "до" (только зоны, без посадок); иначе — схема "после"."""
    kinds = list(constraint_results.keys())
    fig, axes = plt.subplots(1, len(kinds), figsize=(7 * len(kinds), 7))
    if len(kinds) == 1:
        axes = [axes]

    for ax, kind in zip(axes, kinds):
        cr = constraint_results[kind]
        _plot_geometry(ax, cr.site_boundary, facecolor="none", edgecolor="#333333", linewidth=1.2, zorder=1)
        _plot_geometry(ax, cr.forbidden_zone, facecolor="#e07b6b", edgecolor="none", alpha=0.45, zorder=2)
        _plot_geometry(ax, cr.allowed_zone, facecolor="#6fae6a", edgecolor="none", alpha=0.55, zorder=3)

        if placements is not None:
            pts = placements.get(kind, [])
            placed = [p for p in pts if p.status == "placed"]
            rejected = [p for p in pts if p.status != "placed"]
            if placed:
                ax.scatter(
                    [p.x for p in placed], [p.y for p in placed],
                    c="#1f4d28", s=18, zorder=5, label="Предложено",
                )
            if rejected:
                ax.scatter(
                    [p.x for p in rejected], [p.y for p in rejected],
                    c="#888888", s=10, zorder=4, marker="x", label="Отклонено",
                )
            if placed or rejected:
                ax.legend(loc="upper right", fontsize=8, framealpha=0.9)

        ax.set_title(f"{title} — {_KIND_LABEL_RU.get(kind, kind)}", fontsize=12)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)


def render_human_report(
    report: dict,
    constraint_results: dict[str, ConstraintMapResult],
    placement_results: dict[str, PlacementResult],
    output_dir: str | Path,
    object_label: str = "",
) -> HumanReportPaths:
    """Собирает человекочитаемый отчёт (Markdown + HTML) из уже посчитанного
    JSON-отчёта (`report`, см. pipeline/report/interpretation_report.py) и
    геометрий Constraint Engine (только для схемы "до/после" — в самом JSON
    геометрий зон нет, только координаты точек)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    placements_by_kind = {kind: pr.points for kind, pr in placement_results.items()}

    scheme_before_path = output_dir / "scheme_before.png"
    scheme_after_path = output_dir / "scheme_after.png"
    _render_scheme_image(constraint_results, None, "Допустимая зона (до посадки)", scheme_before_path)
    _render_scheme_image(constraint_results, placements_by_kind, "После размещения", scheme_after_path)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), trim_blocks=True, lstrip_blocks=True)
    context = {
        "object_label": object_label,
        "report": report,
        "scheme_before_name": scheme_before_path.name,
        "scheme_after_name": scheme_after_path.name,
    }

    markdown_path = output_dir / "human_report.md"
    markdown_path.write_text(env.get_template("human_report.md.j2").render(**context), encoding="utf-8")

    html_path = output_dir / "human_report.html"
    html_path.write_text(env.get_template("human_report.html.j2").render(**context), encoding="utf-8")

    return HumanReportPaths(
        markdown_path=markdown_path,
        html_path=html_path,
        scheme_before_path=scheme_before_path,
        scheme_after_path=scheme_after_path,
    )
