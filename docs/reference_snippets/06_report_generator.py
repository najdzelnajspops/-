"""
Агент 06 — Генерация Markdown и PDF инвестиционного отчёта.

Рендерит Jinja2-шаблон → Markdown → PDF (fpdf2).
Включает рекомендации из агента 07 (если уже выполнен).
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from utils.pipeline_context import BaseAgent, PipelineContext
from utils.pipeline_contracts import validate_required_paths


class ReportGeneratorAgent(BaseAgent):

    def run(self) -> PipelineContext:
        ctx = self.ctx

        feat    = ctx.data.get("features")           or self._load_json(ctx.get_path("features"))
        dcf     = ctx.data.get("dcf")                or self._load_json(ctx.get_path("dcf"))
        mc      = ctx.data.get("monte_carlo")        or self._load_json(ctx.get_path("monte_carlo"))
        obj     = ctx.data.get("object")             or self._load_json(ctx.get_path("object"))
        macro   = ctx.data.get("macro")              or self._load_json(ctx.get_path("macro"))
        comp_path = ctx.get_path("comparables")
        comp    = ctx.data.get("comparables")        or self._load_json_safe(comp_path)
        alcohol = ctx.data.get("alcohol_compliance") or self._load_json_safe(ctx.get_path("alcohol_compliance"))

        self.logger.info("[06] Генерация отчёта")
        self._validate_report_contract(feat, dcf, mc, obj, macro)

        tpl_dir = ctx.base_dir / "config" / "templates"
        env     = Environment(loader=FileSystemLoader(str(tpl_dir)))
        template = env.get_template("report_template.md.j2")

        vars_ = self._build_vars(feat, dcf, mc, obj, macro, comp, alcohol, comp_path.exists())
        report_md = template.render(**vars_)

        # Включаем рекомендации
        rec_path = ctx.get_path("recommendations")
        recommendation_status = vars_.get("optional_blocks", {}).get("recommendations", {})
        if rec_path.exists():
            rec_md = rec_path.read_text(encoding="utf-8")
            report_md = report_md.rstrip() + "\n\n---\n\n" + rec_md
            recommendation_status["status"] = "included"
            recommendation_status["message"] = "Файл рекомендаций включён в итоговый отчёт"
            self.logger.info("[06] Рекомендации включены")
        else:
            if recommendation_status.get("status") == "expected_missing":
                self.logger.warning("[06] recommendations.md ожидался, но не найден — отчёт без блока рекомендаций")
            else:
                self.logger.warning("[06] recommendations.md не найден, отчёт будет без блока рекомендаций")

        out_path = ctx.get_path("report")
        out_path.write_text(report_md, encoding="utf-8")
        self.logger.info("[06] Отчёт: %s", out_path)

        pdf_path = out_path.with_suffix(".pdf")
        self._try_export_pdf(report_md, pdf_path)

        ctx.data["report_path"] = str(out_path)
        return ctx

    # ------------------------------------------------------------------

    def _validate_report_contract(self, feat, dcf, mc, obj, macro) -> None:
        validate_required_paths(
            "object -> agent06",
            obj,
            [
                "address",
                "area_m2",
                "analysis_years",
                "acquisition_costs_rub",
            ],
            numeric_paths=[
                "area_m2",
                "analysis_years",
                "acquisition_costs_rub",
            ],
        )
        validate_required_paths(
            "features -> agent06",
            feat,
            [
                "price_asking_rub",
                "listing_discount",
                "price_effective_rub",
                "price_per_m2",
                "total_investment_rub",
                "monthly_rent_rub",
                "rent_per_m2_mo",
                "vacancy_rate",
                "opex_ratio",
                "gross_income_rub",
                "noi_after_tax_rub",
                "gross_yield",
                "cap_rate",
                "simple_payback_years",
                "location_score",
            ],
            numeric_paths=[
                "price_asking_rub",
                "listing_discount",
                "price_effective_rub",
                "price_per_m2",
                "total_investment_rub",
                "monthly_rent_rub",
                "rent_per_m2_mo",
                "vacancy_rate",
                "opex_ratio",
                "gross_income_rub",
                "noi_after_tax_rub",
                "gross_yield",
                "cap_rate",
                "simple_payback_years",
            ],
        )
        validate_required_paths(
            "macro -> agent06",
            macro,
            ["cbr_key_rate", "inflation_rate", "discount_rate", "risk_premium", "data_date"],
            numeric_paths=["cbr_key_rate", "inflation_rate", "discount_rate", "risk_premium"],
        )
        validate_required_paths(
            "dcf -> agent06",
            dcf,
            [
                "scenarios",
                "scenarios.base",
                "scenarios.base.cash_flows",
                "scenarios.pessimistic",
                "scenarios.optimistic",
            ],
        )
        validate_required_paths(
            "monte_carlo -> agent06",
            mc,
            [
                "n_simulations",
                "probability_npv_positive",
                "risk_assessment",
                "npv.p5",
                "npv.median",
                "npv.p95",
                "var_95_rub",
                "payback_years.median",
            ],
            numeric_paths=[
                "n_simulations",
                "probability_npv_positive",
                "npv.p5",
                "npv.median",
                "npv.p95",
                "var_95_rub",
                "payback_years.median",
            ],
        )

    def _build_vars(self, feat, dcf, mc, obj, macro, comp=None, alcohol=None, comparables_file_exists: bool = False) -> dict:
        sc = dcf.get("scenarios", {})
        scenarios_display = self._build_scenarios_display(sc)
        base = sc.get("base", {})
        local_analogs = self._build_local_analogs_payload(comp or {})
        optional_blocks = self._build_optional_block_status(alcohol, comp, comparables_file_exists)
        payback_explain = self._build_payback_explain(feat, obj)
        report_presentation = self._build_report_presentation(
            feat,
            obj,
            comp or {},
            local_analogs,
            alcohol or {},
            optional_blocks,
            payback_explain,
        )

        return {
            "analysis_date":    datetime.now().strftime("%d.%m.%Y"),
            "object":           obj,
            "feat":             feat,
            "dcf":              dcf,
            "mc":               mc,
            "macro":            macro,
            "base_scenario":    base,
            "scenarios":        sc,
            "scenarios_display": scenarios_display,
            "comp":             comp or {},
            "local_analogs":    local_analogs,
            "alcohol":          alcohol or {},
            "optional_blocks":  optional_blocks,
            "payback_explain":  payback_explain,
            "report_presentation": report_presentation,
        }

    def _build_local_analogs_payload(self, comp: dict) -> dict:
        report = comp.get("nearby_report") or {}
        rows = report.get("relevant_neighbors") or comp.get("relevant_neighbors") or []
        normalized_rows: list[dict] = []
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            deal_type = str(row.get("deal_type") or "").strip().lower()
            normalized_rows.append(
                {
                    "address": row.get("address") or row.get("address_display") or row.get("title") or "—",
                    "source": str(row.get("source") or "—").upper(),
                    "deal_label": "Аренда" if deal_type == "rent" else "Продажа" if deal_type == "sale" else "—",
                    "area_m2": row.get("area_m2") or row.get("area") or "—",
                    "distance_m": row.get("distance_m"),
                    "floor": row.get("floor") or row.get("floor_text") or "—",
                    "price_total_rub": self._to_float(
                        row.get("price_rub")
                        or row.get("monthly_rent_rub")
                    ),
                    "price_per_m2": self._to_float(
                        row.get("price_per_m2")
                        or row.get("rent_per_m2_mo")
                    ),
                }
            )
        return {
            "radius_m": int(report.get("radius_m") or 500),
            "relevant_label": report.get("relevant_label") or "сопоставимые объекты",
            "count": int(report.get("relevant_count") or len(normalized_rows) or 0),
            "rows": normalized_rows,
        }

    def _build_payback_explain(self, feat: dict, obj: dict) -> dict:
        price_effective = self._to_float(feat.get("price_effective_rub"))
        vacancy_rate = self._to_ratio(feat.get("vacancy_rate"), 0.08)
        opex_ratio = self._to_ratio(feat.get("opex_ratio"), 0.18)
        property_tax_rate = self._to_ratio(
            obj.get("property_tax_rate", feat.get("property_tax_rate", 0.02)),
            0.02,
        )
        income_tax_rate = self._to_ratio(
            feat.get("income_tax_rate", obj.get("income_tax_rate", 0.06)),
            0.06,
        )
        model_base_rent = self._to_float(
            feat.get("independent_model_monthly_rent_base_rub")
            or feat.get("monthly_rent_base_rub")
        )
        model_adjusted_rent = self._to_float(
            feat.get("independent_model_monthly_rent_adjusted_rub")
            or feat.get("monthly_rent_adjusted_rub")
            or feat.get("monthly_rent_rub")
        )
        canonical_rent = self._to_float(obj.get("target_monthly_rent_rub"))

        model_base = self._build_payback_case(
            monthly_rent=model_base_rent,
            price_effective=price_effective,
            vacancy_rate=vacancy_rate,
            opex_ratio=opex_ratio,
            property_tax_rate=property_tax_rate,
            income_tax_rate=income_tax_rate,
        )
        model_adjusted = self._build_payback_case(
            monthly_rent=model_adjusted_rent,
            price_effective=price_effective,
            vacancy_rate=vacancy_rate,
            opex_ratio=opex_ratio,
            property_tax_rate=property_tax_rate,
            income_tax_rate=income_tax_rate,
        )
        canonical = (
            self._build_payback_case(
                monthly_rent=canonical_rent,
                price_effective=price_effective,
                vacancy_rate=vacancy_rate,
                opex_ratio=opex_ratio,
                property_tax_rate=property_tax_rate,
                income_tax_rate=income_tax_rate,
            )
            if canonical_rent > 0
            else None
        )

        rent_delta_rub = None
        rent_delta_pct = None
        payback_delta_years = None
        if canonical and model_adjusted:
            rent_delta_rub = round(canonical["monthly_rent_rub"] - model_adjusted["monthly_rent_rub"])
            if model_adjusted["monthly_rent_rub"] > 0:
                rent_delta_pct = round((rent_delta_rub / model_adjusted["monthly_rent_rub"]) * 100, 1)
            if canonical["payback_years"] is not None and model_adjusted["payback_years"] is not None:
                payback_delta_years = round(canonical["payback_years"] - model_adjusted["payback_years"], 1)

        return {
            "has_canonical": bool(canonical),
            "canonical_label": "Указанная аренда в объекте / объявлении",
            "model_label": "Независимая рыночная модель",
            "uses_price_effective_only": True,
            "price_effective_rub": round(price_effective),
            "price_asking_rub": round(self._to_float(feat.get("price_asking_rub"))),
            "total_investment_rub": round(self._to_float(feat.get("total_investment_rub"))),
            "listing_discount_pct": round(self._to_ratio(feat.get("listing_discount"), 0.10) * 100, 1),
            "vacancy_rate_pct": round(vacancy_rate * 100, 1),
            "opex_ratio_pct": round(opex_ratio * 100, 1),
            "property_tax_rate_pct": round(property_tax_rate * 100, 1),
            "income_tax_rate_pct": round(income_tax_rate * 100, 1),
            "model_base": model_base,
            "model_adjusted": model_adjusted,
            "canonical": canonical,
            "rent_delta_rub": rent_delta_rub,
            "rent_delta_pct": rent_delta_pct,
            "payback_delta_years": payback_delta_years,
            "adjustment_multiplier": self._to_float(
                feat.get("independent_model_monthly_rent_adjustment_multiplier")
                or feat.get("monthly_rent_adjustment_multiplier"),
                1.0,
            ),
            "adjustment_points": self._to_float(
                feat.get("independent_model_monthly_rent_adjustment_points")
                or feat.get("monthly_rent_adjustment_points"),
                0.0,
            ),
            "adjustment_reasons": (
                feat.get("independent_model_monthly_rent_adjustment_reasons")
                or feat.get("monthly_rent_adjustment_reasons")
                or []
            ),
        }

    def _build_payback_case(
        self,
        monthly_rent: float,
        price_effective: float,
        vacancy_rate: float,
        opex_ratio: float,
        property_tax_rate: float,
        income_tax_rate: float,
    ) -> dict:
        annual_rent = monthly_rent * 12
        gross_income = annual_rent * (1 - vacancy_rate)
        opex_rub = gross_income * opex_ratio
        noi_before_tax = gross_income - opex_rub
        property_tax_rub = price_effective * property_tax_rate
        income_tax_rub = noi_before_tax * income_tax_rate
        noi_after_tax = noi_before_tax - property_tax_rub - income_tax_rub
        payback_years = round(price_effective / noi_after_tax, 1) if noi_after_tax > 0 else None
        return {
            "monthly_rent_rub": round(monthly_rent),
            "annual_rent_rub": round(annual_rent),
            "gross_income_rub": round(gross_income),
            "opex_rub": round(opex_rub),
            "noi_before_tax_rub": round(noi_before_tax),
            "property_tax_rub": round(property_tax_rub),
            "income_tax_rub": round(income_tax_rub),
            "noi_after_tax_rub": round(noi_after_tax),
            "payback_years": payback_years,
        }

    def _to_float(self, value, default: float = 0.0) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return float(default)

    def _to_ratio(self, value, default: float) -> float:
        ratio = self._to_float(value, default)
        if ratio > 1:
            ratio /= 100
        return ratio

    def _build_scenarios_display(self, scenarios: dict) -> dict:
        display: dict[str, dict] = {}
        for sc_key, sc in (scenarios or {}).items():
            sc_display = dict(sc or {})
            irr = sc.get("irr") if isinstance(sc, dict) else None
            payback_discounted = sc.get("payback_discounted_years") if isinstance(sc, dict) else None
            sc_display["irr_display"] = (
                f"{round(float(irr) * 100, 1)}%"
                if irr not in (None, "")
                else "не рассчитано"
            )
            sc_display["payback_discounted_display"] = (
                f"{payback_discounted} лет"
                if payback_discounted not in (None, "")
                else "не рассчитано"
            )
            display[sc_key] = sc_display
        return display

    def _build_optional_block_status(self, alcohol: dict | None, comp: dict | None, comparables_file_exists: bool) -> dict:
        ctx = self.ctx
        rec_path = ctx.get_path("recommendations")
        recommendations_generated = bool(ctx.data.get("recommendations_generated"))
        recommendations_expected = bool(
            ctx.data.get("recommendations_expected")
            or ctx.data.get("recommendations_path")
            or recommendations_generated
        )
        if rec_path.exists():
            recommendations = {
                "status": "included",
                "label": "Рекомендации",
                "message": "Файл рекомендаций найден и будет включён",
            }
        elif recommendations_expected:
            recommendations = {
                "status": "expected_missing",
                "label": "Рекомендации",
                "message": "Рекомендации ожидались, но файл отсутствует",
            }
        else:
            recommendations = {
                "status": "not_requested",
                "label": "Рекомендации",
                "message": "Блок рекомендаций не запрашивался отдельным агентом",
            }

        alcohol_payload = alcohol or {}
        if alcohol_payload.get("requested"):
            if alcohol_payload.get("error"):
                alcohol_status = {
                    "status": "error",
                    "label": "Алкогольная лицензия",
                    "message": f"Проверка запрошена, но завершилась с ошибкой: {alcohol_payload.get('error')}",
                }
            else:
                alcohol_status = {
                    "status": "included",
                    "label": "Алкогольная лицензия",
                    "message": "Проверка запрошена и её результат включён в отчёт",
                }
        else:
            alcohol_status = {
                "status": "not_requested",
                "label": "Алкогольная лицензия",
                "message": "Проверка лицензии не запрашивалась",
            }

        comp_payload = comp or {}
        sale_list = comp_payload.get("sale_listings") or []
        rent_list = comp_payload.get("rent_listings") or []
        quality = comp_payload.get("quality") or {}
        quality_warnings = quality.get("warnings") or []
        sale_conf = quality.get("sale_confidence")
        rent_conf = quality.get("rent_confidence")
        if not comparables_file_exists and not comp_payload:
            comparables_status = {
                "status": "missing_file",
                "label": "Аналоги рынка",
                "message": "Файл аналогов отсутствует, отчёт собран без слоя рыночных аналогов",
            }
        elif not sale_list and not rent_list:
            comparables_status = {
                "status": "empty",
                "label": "Аналоги рынка",
                "message": "Слой рыночных аналогов присутствует, но списки продажи и аренды пусты",
            }
        elif quality_warnings or sale_conf in {"none", "low"} or rent_conf in {"none", "low"}:
            comparables_status = {
                "status": "low_confidence",
                "label": "Аналоги рынка",
                "message": (
                    "Слой рыночных аналогов загружен, но качество опоры слабое"
                    f" (продажа={sale_conf or 'нет данных'}, аренда={rent_conf or 'нет данных'}, замечания={', '.join(quality_warnings) or 'нет'})"
                ),
            }
        else:
            comparables_status = {
                "status": "available",
                "label": "Аналоги рынка",
                "message": (
                    f"Слой рыночных аналогов загружен: продажа={len(sale_list)}"
                    f", аренда={len(rent_list)}, надёжность продажи={sale_conf or 'нет данных'}, надёжность аренды={rent_conf or 'нет данных'}"
                ),
            }

        return {
            "recommendations": recommendations,
            "alcohol": alcohol_status,
            "comparables": comparables_status,
        }

    def _build_report_presentation(
        self,
        feat: dict,
        obj: dict,
        comp: dict,
        local_analogs: dict,
        alcohol: dict,
        optional_blocks: dict,
        payback_explain: dict,
    ) -> dict:
        executive_summary: list[str] = []
        risks: list[str] = []
        appendix_rows: list[dict[str, str]] = []

        canonical = payback_explain.get("canonical")
        model_adjusted = payback_explain.get("model_adjusted") or {}
        canonical_payback = canonical.get("payback_years") if canonical else None
        model_payback = model_adjusted.get("payback_years")

        if canonical_payback is not None:
            executive_summary.append(
                f"Основной ориентир по объявлению: {canonical_payback} лет простой окупаемости "
                f"({payback_explain.get('canonical_label', 'канон объявления')})."
            )
            if model_payback is not None:
                executive_summary.append(
                    f"Независимая модель даёт {model_payback} лет и используется как проверка / контрфакт."
                )
        elif model_payback is not None:
            executive_summary.append(
                f"Явный арендный ориентир в объявлении отсутствует; основной ориентир строится по модели: {model_payback} лет."
            )
        else:
            executive_summary.append(
                "Явный ориентир в объявлении отсутствует, а модель не даёт устойчивой оценки окупаемости; вывод ограничен."
            )

        sale_count = int(self._to_float(feat.get("comps_sale_count"), 0))
        rent_count = int(self._to_float(feat.get("comps_rent_count"), 0))
        executive_summary.append(
            f"Рыночная опора отчёта: аналогов продажи — {sale_count}, аналогов аренды — {rent_count}."
        )

        if alcohol.get("requested"):
            if alcohol.get("error"):
                executive_summary.append(f"Проверка ФЗ-171 запрошена, но завершилась с ошибкой: {alcohol.get('error')}.")
            elif alcohol.get("compliant"):
                executive_summary.append("Проверка ФЗ-171 не выявила запретительных объектов в радиусе 100 м.")
            else:
                executive_summary.append("Проверка ФЗ-171 выявила ограничения для алкогольной лицензии.")

        if not canonical:
            risks.append("В объявлении отсутствует явный ориентир по аренде или окупаемости, поэтому основной вывод строится по модели.")
        if optional_blocks.get("comparables", {}).get("status") in {"missing_file", "empty", "low_confidence"}:
            risks.append(optional_blocks["comparables"]["message"])
        if sale_count == 0:
            risks.append("Объекты продажи по текущему набору данных не найдены или не используются как надёжная опора.")
        if rent_count == 0:
            risks.append("Объекты аренды по текущему набору данных не найдены; арендная основа может опираться на эвристику.")
        if alcohol.get("requested") and alcohol.get("error"):
            risks.append(f"Блок ФЗ-171 неполон: {alcohol.get('error')}.")
        if feat.get("foot_traffic_score") in (None, ""):
            risks.append("Данные по пешеходному трафику отсутствуют или не были построены.")

        if not risks:
            risks.append("Критичных ограничений по данным не выявлено; все ключевые контуры отчёта заполнены.")

        appendix_rows.append(
            {
                "layer": "Основной источник окупаемости",
                "status": "canonical" if canonical else "model",
                "comment": (
                    payback_explain.get("canonical_label", "канон объявления")
                    if canonical
                    else payback_explain.get("model_label", "независимая рыночная модель")
                ),
            }
        )
        appendix_rows.append(
            {
                "layer": optional_blocks["comparables"]["label"],
                "status": optional_blocks["comparables"]["status"],
                "comment": optional_blocks["comparables"]["message"],
            }
        )
        appendix_rows.append(
            {
                "layer": optional_blocks["alcohol"]["label"],
                "status": optional_blocks["alcohol"]["status"],
                "comment": optional_blocks["alcohol"]["message"],
            }
        )
        appendix_rows.append(
            {
                "layer": optional_blocks["recommendations"]["label"],
                "status": optional_blocks["recommendations"]["status"],
                "comment": optional_blocks["recommendations"]["message"],
            }
        )

        return {
            "executive_summary": executive_summary,
            "risks": risks,
            "appendix_rows": appendix_rows,
            "sparse_blocks": self._build_sparse_block_fallbacks(
                comp=comp,
                local_analogs=local_analogs,
                payback_explain=payback_explain,
            ),
        }

    def _build_sparse_block_fallbacks(self, comp: dict, local_analogs: dict, payback_explain: dict) -> dict:
        sale_list = comp.get("sale_listings") or []
        rent_list = comp.get("rent_listings") or []
        local_rows = local_analogs.get("rows") or []
        canonical = payback_explain.get("canonical")
        model_adjusted = payback_explain.get("model_adjusted") or {}

        return {
            "price_analysis": {
                "show_fallback": not bool(sale_list),
                "title": 'Недостаточно данных для блока "Ценовой анализ"',
                "message": "Валидные sale аналоги не найдены; вместо полноценного ценового сопоставления доступны только базовые входные параметры объекта.",
            },
            "local_analogs": {
                "show_fallback": not bool(sale_list or rent_list or local_rows),
                "title": 'Недостаточно данных для блока "Локальные аналоги"',
                "message": "Списки аналогов продажи и аренды пусты или отсутствуют; блок остаётся в отчёте как явный индикатор нехватки данных.",
            },
            "payback_analysis": {
                "show_fallback": canonical is None and model_adjusted.get("payback_years") is None,
                "title": 'Недостаточно данных для блока "Анализ окупаемости"',
                "message": "Отсутствуют и явный сигнал из объявления, и устойчивая модельная оценка; аналитика окупаемости ограничена.",
            },
        }

    def _try_export_pdf(self, report_md: str, pdf_path: Path) -> None:
        try:
            import warnings
            from fpdf import FPDF
        except ImportError:
            self.logger.info("[06] fpdf2 не установлен: pip install fpdf2")
            return

        font_files = self._resolve_pdf_font_files()
        if not font_files:
            self.logger.warning("[06] Шрифт не найден, PDF пропущен")
            return

        font_local = {}
        for style_key, src in font_files.items():
            suffix = style_key.lower() or "regular"
            dst = pdf_path.parent / f"report_pdf_{suffix}.ttf"
            if not dst.exists():
                shutil.copy2(str(src), str(dst))
            font_local[style_key] = dst

        import logging as _lg
        _lg.getLogger("fontTools").setLevel(_lg.WARNING)

        from utils.md_to_pdf import render as md_render

        md_clean = self._sanitize_markdown_for_pdf(report_md)

        class _PDF(FPDF):
            def footer(self_):
                self_.set_y(-12)
                self_.set_font("ReportSans", size=8)
                self_.set_text_color(128)
                self_.cell(0, 10, f"Стр. {self_.page_no()}", align="C")

        pdf = _PDF(format="A4")
        pdf.set_margins(14, 18, 14)
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.add_page()
        for style in ("", "B", "I", "BI"):
            pdf.add_font("ReportSans", style=style, fname=str(font_local[style]))
        pdf.set_font("ReportSans", size=9)

        try:
            import tempfile, os
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                md_render(pdf, md_clean)
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=pdf_path.parent)
            os.close(tmp_fd)
            pdf.output(tmp_path)
            if pdf_path.exists():
                pdf_path.unlink()
            Path(tmp_path).replace(pdf_path)
            self.logger.info("[06] PDF: %s", pdf_path)
        except Exception as exc:
            self.logger.warning("[06] PDF ошибка: %s", exc)

    @staticmethod
    def _sanitize_markdown_for_pdf(text: str) -> str:
        import re
        replacements = {
            "✅": "[+]",
            "❌": "[-]",
            "📌": ">>",
            "💡": "(i)",
            "⚠️": "[!]",
            "⚠": "[!]",
        }
        for e, r in replacements.items():
            text = text.replace(e, r)
        text = text.replace("\ufe0f", "")
        return re.sub(r"[\U00010000-\U0010ffff]", "", text)

    @staticmethod
    def _resolve_pdf_font_files() -> dict[str, Path] | None:
        fonts_dir = Path(r"C:\Windows\Fonts")
        families = [
            {
                "": fonts_dir / "segoeui.ttf",
                "B": fonts_dir / "segoeuib.ttf",
                "I": fonts_dir / "segoeuii.ttf",
                "BI": fonts_dir / "segoeuiz.ttf",
            },
            {
                "": fonts_dir / "arial.ttf",
                "B": fonts_dir / "arialbd.ttf",
                "I": fonts_dir / "ariali.ttf",
                "BI": fonts_dir / "arialbi.ttf",
            },
            {
                "": fonts_dir / "calibri.ttf",
                "B": fonts_dir / "calibrib.ttf",
                "I": fonts_dir / "calibrii.ttf",
                "BI": fonts_dir / "calibriz.ttf",
            },
            {
                "": fonts_dir / "verdana.ttf",
                "B": fonts_dir / "verdanab.ttf",
                "I": fonts_dir / "verdanai.ttf",
                "BI": fonts_dir / "verdanaz.ttf",
            },
        ]
        for family in families:
            regular = family[""]
            if not regular.exists():
                continue
            return {
                style: (path if path.exists() else regular)
                for style, path in family.items()
            }
        return None
