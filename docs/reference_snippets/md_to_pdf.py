"""
Кастомный Markdown → fpdf2 рендерер со стилизацией:
  H1  — крупный заголовок, тёмный цвет, нижняя линия
  H2  — белый текст на тёмном фоне (#1a1a2e)
  H3  — синий (#4C72B0) текст, синяя полоса слева
  Table — синяя шапка, чередующиеся строки
  HR  — тонкая серая линия
  > blockquote — светло-голубой фон, синяя полоса слева
  - list — маркированный список
  ```code``` — серый фон, моноширинный вид
  **bold** inline — жирный шрифт внутри параграфа
"""
import re
from fpdf import FPDF

# ─── Цвета ─────────────────────────────────────────────────────────────────
C_DARK      = (26,  26,  46)    # #1a1a2e
C_BLUE      = (76,  114, 176)   # #4C72B0
C_WHITE     = (255, 255, 255)
C_TEXT      = (26,  26,  46)
C_GRAY_LINE = (192, 200, 216)   # линии таблицы
C_ROW_ALT   = (244, 247, 252)   # чётные строки
C_QUOTE_BG  = (238, 242, 251)   # blockquote фон
C_CODE_BG   = (240, 240, 240)   # code block фон
C_STRONG    = (15,  52,  96)    # #0f3460 для bold

_BOTTOM_MARGIN = 16  # мм — должен совпадать с set_auto_page_break(margin=16) в агенте


def render(pdf: FPDF, md_text: str) -> None:
    """Рендерит Markdown в уже открытый документ fpdf2."""
    blocks = _parse(md_text)
    for block in blocks:
        _dispatch(pdf, block)


# ─── Парсер ────────────────────────────────────────────────────────────────

def _parse(md: str) -> list:
    blocks = []
    lines  = md.splitlines()
    i      = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # Заголовки
        m = re.match(r'^(#{1,4})\s+(.*)', stripped)
        if m:
            level = len(m.group(1))
            blocks.append({"type": f"h{level}", "text": m.group(2).strip()})
            i += 1
            continue

        # HR
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # Fenced code block (``` или ~~~)
        if re.match(r'^```|^~~~', stripped):
            fence = stripped[:3]
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].rstrip().startswith(fence):
                code_lines.append(lines[i].rstrip())
                i += 1
            if i < len(lines):
                i += 1  # пропускаем закрывающий ограничитель
            if code_lines:
                blocks.append({"type": "code", "lines": code_lines})
            continue

        # Blockquote: "> текст" или ">" (пустой)
        if stripped.startswith(">"):
            qlines = []
            while i < len(lines) and lines[i].rstrip().startswith(">"):
                raw = lines[i].rstrip()
                qlines.append(raw[2:] if raw.startswith("> ") else raw[1:])
                i += 1
            text = " ".join(q for q in qlines if q.strip())
            if text:
                blocks.append({"type": "blockquote", "text": text})
            continue

        # Таблица — строка начинается с |
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            headers, rows = _parse_table(table_lines)
            if headers:
                blocks.append({"type": "table", "headers": headers, "rows": rows})
            continue

        # Список
        if re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+\.\s', stripped):
            items = []
            while i < len(lines):
                m2 = re.match(r'^[-*+]\s+(.*)', lines[i].rstrip())
                m3 = re.match(r'^\d+\.\s+(.*)', lines[i].rstrip())
                if m2:
                    items.append(m2.group(1))
                    i += 1
                elif m3:
                    items.append(m3.group(1))
                    i += 1
                else:
                    break
            blocks.append({"type": "list", "items": items})
            continue

        # Пустая строка
        if not stripped:
            i += 1
            continue

        # Параграф (накапливаем строки до пустой или спецблока)
        plines = []
        while i < len(lines) and lines[i].strip() \
              and not lines[i].strip().startswith("|") \
              and not lines[i].rstrip().startswith(">") \
              and not lines[i].rstrip().startswith("```") \
              and not lines[i].rstrip().startswith("~~~") \
              and not re.match(r'^#{1,4}\s', lines[i]) \
              and not re.match(r'^[-*_]{3,}\s*$', lines[i]) \
              and not re.match(r'^[-*+]\s', lines[i]):
            plines.append(lines[i].rstrip())
            i += 1
        if plines:
            blocks.append({"type": "p", "text": " ".join(plines)})
        elif i < len(lines):
            # Защита от зависания: строка не попала ни в один блок — пропускаем
            i += 1

    return blocks


def _parse_table(lines: list) -> tuple:
    """Парсит список строк таблицы в (headers, rows)."""
    if len(lines) < 2:
        return [], []
    def split_row(line):
        cells = [c.strip() for c in line.strip("|").split("|")]
        return cells
    headers = split_row(lines[0])
    rows = []
    for line in lines[2:]:          # строка 1 — разделитель ---
        if re.match(r'^[\s|:-]+$', line):
            continue
        rows.append(split_row(line))
    return headers, rows


# ─── Диспетчер ─────────────────────────────────────────────────────────────

def _dispatch(pdf: FPDF, block: dict) -> None:
    t = block["type"]
    if   t == "h1":         _render_h1(pdf, block["text"])
    elif t == "h2":         _render_h2(pdf, block["text"])
    elif t == "h3":         _render_h3(pdf, block["text"])
    elif t == "h4":         _render_h4(pdf, block["text"])
    elif t == "hr":         _render_hr(pdf)
    elif t == "blockquote": _render_blockquote(pdf, block["text"])
    elif t == "table":      _render_table(pdf, block["headers"], block["rows"])
    elif t == "list":       _render_list(pdf, block["items"])
    elif t == "code":       _render_code(pdf, block["lines"])
    elif t == "p":          _render_p(pdf, block["text"])


# ─── Вспомогательные ───────────────────────────────────────────────────────

def _page_bottom(pdf: FPDF) -> float:
    """Нижняя граница контента (с учётом footer)."""
    return pdf.h - _BOTTOM_MARGIN


def _ensure_space(pdf: FPDF, needed: float) -> None:
    """Переходит на новую страницу если места меньше needed мм."""
    if pdf.get_y() + needed > _page_bottom(pdf):
        pdf.add_page()


def _plain(text: str) -> str:
    """Удаляет Markdown-разметку для вывода в fpdf2."""
    # Bold/italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Links [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _font_name(pdf: FPDF) -> str:
    current = getattr(pdf, "font_family", "") or ""
    return current or "Arial"


# ─── Рендереры блоков ──────────────────────────────────────────────────────

def _render_h1(pdf: FPDF, text: str) -> None:
    _ensure_space(pdf, 20)
    pdf.ln(4)
    pdf.set_font(_font_name(pdf), style="B", size=16)
    pdf.set_text_color(*C_DARK)
    pdf.multi_cell(0, 8, _plain(text), align="L")
    y = pdf.get_y()
    pdf.set_draw_color(*C_BLUE)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_line_width(0.2)
    pdf.ln(4)


def _render_h2(pdf: FPDF, text: str) -> None:
    # H2 внизу страницы — переносим на следующую
    if pdf.get_y() > _page_bottom(pdf) - 30:
        pdf.add_page()
    pdf.ln(6)
    lm, rm = pdf.l_margin, pdf.r_margin
    w  = pdf.w - lm - rm
    h  = 9
    y  = pdf.get_y()
    pdf.set_fill_color(*C_DARK)
    pdf.rect(lm, y, w, h, style="F")
    pdf.set_text_color(*C_WHITE)
    pdf.set_font(_font_name(pdf), style="B", size=11)
    pdf.set_xy(lm + 3, y + 1.5)
    pdf.cell(w - 3, h - 1, _plain(text), align="L")
    pdf.set_text_color(*C_TEXT)
    pdf.ln(h + 3)


def _render_h3(pdf: FPDF, text: str) -> None:
    if pdf.get_y() > _page_bottom(pdf) - 20:
        pdf.add_page()
    pdf.ln(4)
    lm = pdf.l_margin
    y  = pdf.get_y()
    pdf.set_fill_color(*C_BLUE)
    pdf.rect(lm, y, 2.5, 6.5, style="F")
    pdf.set_text_color(*C_BLUE)
    pdf.set_font(_font_name(pdf), style="B", size=10)
    pdf.set_xy(lm + 5, y)
    pdf.multi_cell(0, 6.5, _plain(text), align="L")
    pdf.set_text_color(*C_TEXT)
    pdf.ln(2)


def _render_h4(pdf: FPDF, text: str) -> None:
    _ensure_space(pdf, 10)
    pdf.ln(2)
    pdf.set_font(_font_name(pdf), style="B", size=9)
    pdf.set_text_color(*C_DARK)
    pdf.multi_cell(0, 5, _plain(text), align="L")
    pdf.set_text_color(*C_TEXT)
    pdf.ln(1)


def _render_hr(pdf: FPDF) -> None:
    pdf.ln(3)
    pdf.set_draw_color(*C_GRAY_LINE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(3)


def _render_blockquote(pdf: FPDF, text: str) -> None:
    pdf.ln(2)
    lm = pdf.l_margin
    pdf.set_font(_font_name(pdf), style="I", size=9)
    text_plain = _plain(text)
    w = pdf.w - lm - pdf.r_margin - 10
    lines_n = max(1, int(pdf.get_string_width(text_plain) / w) + 1) if text_plain else 1
    h = float(lines_n) * 5 + 6

    _ensure_space(pdf, h + 4)
    y0 = pdf.get_y()

    pdf.set_fill_color(*C_QUOTE_BG)
    pdf.rect(lm, y0, pdf.w - lm - pdf.r_margin, h, style="F")
    pdf.set_fill_color(*C_BLUE)
    pdf.rect(lm, y0, 2.5, h, style="F")

    pdf.set_text_color(42, 58, 94)
    pdf.set_xy(lm + 6, y0 + 2)
    pdf.multi_cell(w, 5, text_plain, align="L")
    pdf.set_text_color(*C_TEXT)
    pdf.ln(3)


def _render_code(pdf: FPDF, code_lines: list) -> None:
    """Рендерит fenced code block — серый фон, текст как есть."""
    if not code_lines:
        return
    pdf.ln(2)
    lm, rm = pdf.l_margin, pdf.r_margin
    w = pdf.w - lm - rm
    line_h = 4.5
    pad = 3
    total_h = float(len(code_lines)) * line_h + pad * 2

    _ensure_space(pdf, total_h + 4)
    y0 = pdf.get_y()

    pdf.set_fill_color(*C_CODE_BG)
    pdf.rect(lm, y0, w, total_h, style="F")

    pdf.set_font(_font_name(pdf), style="", size=8)
    pdf.set_text_color(*C_TEXT)
    pdf.set_xy(lm + pad, y0 + pad)
    for cline in code_lines:
        plain = _plain(cline) if cline.strip() else ""
        pdf.set_x(lm + pad)
        if plain:
            pdf.cell(w - pad * 2, line_h, plain[:120], align="L")
        pdf.ln(line_h)

    pdf.set_xy(lm, y0 + total_h)
    pdf.ln(3)


def _render_table(pdf: FPDF, headers: list, rows: list) -> None:
    if not headers:
        return
    pdf.ln(3)
    lm, rm = pdf.l_margin, pdf.r_margin
    tbl_w  = pdf.w - lm - rm
    n_cols = len(headers)
    col_w  = tbl_w / n_cols
    pb     = _page_bottom(pdf)

    # Сохраняем b_margin до отключения — иначе set_auto_page_break(False)
    # обнуляет его и при восстановлении контент залезает на footer
    saved_b_margin = pdf.b_margin
    pdf.set_auto_page_break(False)

    def draw_header():
        y = pdf.get_y()
        pdf.set_fill_color(*C_BLUE)
        pdf.rect(lm, y, tbl_w, 7, style="F")
        pdf.set_text_color(*C_WHITE)
        pdf.set_font(_font_name(pdf), style="B", size=8)
        for j, h in enumerate(headers):
            pdf.set_xy(lm + j * col_w + 1.5, y + 1)
            pdf.cell(col_w - 3, 5, _plain(h)[:40], align="L")
        pdf.set_xy(lm, y + 7)

    first_row_h = _estimate_row_height(pdf, rows[0], col_w) if rows else 6
    if pdf.get_y() + 7 + first_row_h > pb:
        pdf.add_page()

    draw_header()

    pdf.set_font(_font_name(pdf), style="", size=8)
    for idx, row in enumerate(rows):
        row_h = _estimate_row_height(pdf, row, col_w)
        y = pdf.get_y()

        if y + row_h > pb:
            pdf.add_page()
            draw_header()
            y = pdf.get_y()

        if idx % 2 == 1:
            pdf.set_fill_color(*C_ROW_ALT)
            pdf.rect(lm, y, tbl_w, row_h, style="F")

        pdf.set_draw_color(*C_GRAY_LINE)
        pdf.set_line_width(0.2)
        pdf.set_text_color(*C_TEXT)

        for j, cell in enumerate(row):
            pdf.set_xy(lm + j * col_w + 1.5, y + 1)
            cell_text = _plain(cell) if j < len(row) else ""
            pdf.multi_cell(col_w - 3, 4.5, cell_text, align="L",
                           max_line_height=row_h - 2)
            if j < n_cols - 1:
                pdf.line(lm + (j+1)*col_w, y, lm + (j+1)*col_w, y + row_h)

        pdf.line(lm, y + row_h, lm + tbl_w, y + row_h)
        pdf.set_xy(lm, y + row_h)

    pdf.set_auto_page_break(True, margin=saved_b_margin)
    pdf.ln(3)


def _estimate_row_height(pdf: FPDF, row: list, col_w: float) -> float:
    """Оценивает высоту строки по самой длинной ячейке."""
    max_lines = 1
    for cell in row:
        text = _plain(cell)
        if col_w > 3 and text:
            n = max(1, int(pdf.get_string_width(text) / (col_w - 3)) + 1)
            max_lines = max(max_lines, n)
    return float(max_lines) * 4.5 + 2


def _render_list(pdf: FPDF, items: list) -> None:
    pdf.ln(1)
    pdf.set_font(_font_name(pdf), style="", size=9)
    pdf.set_text_color(*C_TEXT)
    for item in items:
        item_plain = _plain(item)
        # Оценка высоты элемента списка
        usable_w = pdf.w - pdf.r_margin - pdf.l_margin - 5
        lines_n = max(1, int(pdf.get_string_width(item_plain) / usable_w) + 1) if item_plain else 1
        item_h = lines_n * 5
        _ensure_space(pdf, item_h + 2)

        x0 = pdf.l_margin
        pdf.set_xy(x0, pdf.get_y())
        pdf.cell(4, 5, "\u2022", align="C")
        pdf.set_x(x0 + 5)
        pdf.multi_cell(usable_w, 5, item_plain, align="L")
    pdf.ln(1)


def _render_p(pdf: FPDF, text: str) -> None:
    """Параграф — с проверкой места перед рендером."""
    pdf.ln(1)
    plain = _plain(text)
    pdf.set_font(_font_name(pdf), style="", size=9)
    # Оценка высоты параграфа
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    lines_n = max(1, int(pdf.get_string_width(plain) / usable_w) + 1) if plain else 1
    needed = lines_n * 5
    _ensure_space(pdf, needed + 2)

    pdf.set_text_color(*C_TEXT)
    pdf.multi_cell(0, 5, plain, align="L")
    pdf.ln(1)
