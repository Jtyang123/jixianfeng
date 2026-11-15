"""把 docs/项目白皮书.md 生成为排版规范的白皮书 DOCX。

只依赖 python-docx。转换完成后用 Word 导出 PDF：

    powershell -c "$w=New-Object -ComObject Word.Application;
      $d=$w.Documents.Open('docs\\赛题一+OKC+急先锋_项目白皮书.docx');
      $d.SaveAs('docs\\赛题一+OKC+急先锋_项目白皮书.pdf',17); $d.Close(); $w.Quit()"
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = DOCS / "项目白皮书.md"
TARGET = DOCS / "赛题一+OKC+急先锋_项目白皮书.docx"

HEI = "黑体"
SONG = "宋体"
MONO = "Consolas"
INK = RGBColor(0x1A, 0x1A, 0x1A)
ACCENT = RGBColor(0x1B, 0x36, 0x7A)


def set_run(run, size: float, bold: bool = False, east: str = SONG,
            ascii_font: str = "Times New Roman", color: RGBColor = INK) -> None:
    run.font.name = ascii_font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:eastAsia"), east)
    fonts.set(qn("w:ascii"), ascii_font)
    fonts.set(qn("w:hAnsi"), ascii_font)


def add_inline(paragraph, text: str, size: float, east: str = SONG, bold: bool = False) -> None:
    """处理 **粗体** 和 `代码` 两种行内标记。"""
    for chunk in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            run = paragraph.add_run(chunk[2:-2])
            set_run(run, size, True, HEI if east == HEI else SONG, "Times New Roman", INK)
        elif chunk.startswith("`") and chunk.endswith("`"):
            run = paragraph.add_run(chunk[1:-1])
            set_run(run, size - 0.5, bold, east, MONO, RGBColor(0x33, 0x33, 0x33))
        else:
            run = paragraph.add_run(chunk)
            set_run(run, size, bold, east)


def shade(cell, hex_color: str) -> None:
    element = OxmlElement("w:shd")
    element.set(qn("w:val"), "clear")
    element.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(element)


def add_page_number(section) -> None:
    paragraph = section.footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)
    for run in paragraph.runs:
        set_run(run, 9, False, SONG, "Times New Roman", RGBColor(0x66, 0x66, 0x66))


def rule_under(paragraph, color: str = "1B367A") -> None:
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _display_width(text: str) -> int:
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _apply_table_layout(table, rows: list[list[str]], usable_cm: float = 15.6) -> None:
    columns = len(rows[0])
    weights = []
    for c in range(columns):
        widest = max(_display_width(row[c]) if c < len(row) else 2 for row in rows)
        weights.append(min(max(widest, 6), 26))
    total = sum(weights) or 1
    widths = [max(usable_cm * weight / total, 1.9) for weight in weights]
    scale = usable_cm / sum(widths)
    widths = [width * scale for width in widths]

    for row in table.rows:
        for index, width in enumerate(widths):
            if index < len(row.cells):
                row.cells[index].width = Cm(width)

    for row in table.rows:
        properties = row._tr.get_or_add_trPr()
        cant_split = OxmlElement("w:cantSplit")
        cant_split.set(qn("w:val"), "true")
        properties.append(cant_split)

    header = table.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    header.append(repeat)

    # 除最后一行外，行内段落"与下段同页"，避免表格被拆到两页
    for row in table.rows[:-1]:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.keep_with_next = True


def build() -> Path:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    doc = Document()

    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21.0)
    section.top_margin, section.bottom_margin = Cm(2.54), Cm(2.54)
    section.left_margin, section.right_margin = Cm(2.8), Cm(2.6)

    normal = doc.styles["Normal"]
    normal.font.name = SONG
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), SONG)

    index = 0
    table_buffer: list[str] = []

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in table_buffer]
        table_buffer = []
        if len(rows) >= 2 and set(rows[1][0]) <= set("-: "):
            rows = [rows[0]] + rows[2:]
        if not rows:
            return
        table = doc.add_table(rows=len(rows), cols=len(rows[0]))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if c >= len(rows[0]):
                    continue
                cell = table.cell(r, c)
                cell.text = ""
                paragraph = cell.paragraphs[0]
                paragraph.paragraph_format.space_before = Pt(2)
                paragraph.paragraph_format.space_after = Pt(2)
                add_inline(paragraph, value, 8.5 if len(rows[0]) > 3 else 9,
                           HEI if r == 0 else SONG, r == 0)
                if r == 0:
                    shade(cell, "EDF2FB")
        _apply_table_layout(table, rows)
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("|"):
            table_buffer.append(stripped)
            index += 1
            continue
        flush_table()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("# "):
            paragraph = doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(4)
            add_inline(paragraph, stripped[2:], 21, HEI, True)
            for run in paragraph.runs:
                run.font.color.rgb = ACCENT
            rule_under(paragraph)
        elif stripped.startswith("## "):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(16)
            paragraph.paragraph_format.space_after = Pt(7)
            paragraph.paragraph_format.keep_with_next = True
            add_inline(paragraph, stripped[3:], 14.5, HEI, True)
            for run in paragraph.runs:
                run.font.color.rgb = ACCENT
        elif stripped.startswith("### "):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(11)
            paragraph.paragraph_format.space_after = Pt(5)
            paragraph.paragraph_format.keep_with_next = True
            add_inline(paragraph, stripped[4:], 11.5, HEI, True)
        elif stripped.startswith("- "):
            paragraph = doc.add_paragraph(style="List Bullet")
            paragraph.paragraph_format.left_indent = Cm(1.0)
            paragraph.paragraph_format.first_line_indent = Cm(-0.45)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.paragraph_format.line_spacing = 1.55
            add_inline(paragraph, stripped[2:], 10.5)
        elif re.match(r"^\d+\.\s", stripped):
            body = re.sub(r"^\d+\.\s", "", stripped)
            paragraph = doc.add_paragraph(style="List Number")
            paragraph.paragraph_format.left_indent = Cm(1.0)
            paragraph.paragraph_format.first_line_indent = Cm(-0.45)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.paragraph_format.line_spacing = 1.55
            add_inline(paragraph, body, 10.5)
        else:
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.first_line_indent = Cm(0.74)
            paragraph.paragraph_format.space_after = Pt(6)
            paragraph.paragraph_format.line_spacing = 1.6
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            add_inline(paragraph, stripped, 10.5)

        index += 1

    flush_table()
    add_page_number(section)
    doc.save(TARGET)
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"DOCX: {path}")
