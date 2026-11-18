"""生成《项目原创性与权属声明》待签字模板（A4 一页）。

用途：04_相关证明材料 里的可选但建议补的一份材料。生成后打印、三人签字、扫描为 PDF。
只写 docs/ 下的 docx，不碰其他交付物。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "OKC_项目原创性声明_待签字.docx"

FONT = "微软雅黑"
INK = RGBColor(0x14, 0x2A, 0x28)
MUTED = RGBColor(0x5F, 0x6E, 0x68)

MEMBERS = ["杨鄢然（队长）", "赵立薇", "周梦瑶"]
# 签署人：由队长代表本队签署，避免出现"三人签字位只签一人"的半成品观感
SIGNER = "杨鄢然（队长）"

COMPONENTS = [
    ("FastAPI", "0.115.x", "MIT"),
    ("Uvicorn", "0.34.x", "BSD-3-Clause"),
    ("Pydantic", "2.x", "MIT"),
    ("httpx", "0.28.x", "BSD-3-Clause"),
    ("pypdf", "5.x", "BSD-3-Clause"),
    ("psycopg", "3.2.x", "LGPL-3.0"),
    ("Vue", "3.5.x", "MIT"),
    ("Leaflet", "1.9.x", "BSD-2-Clause"),
]


def _font(run, size: float, bold: bool = False, color: RGBColor = INK) -> None:
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)


def _para(doc, text: str, size: float = 10, bold: bool = False, color: RGBColor = INK,
          align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after: float = 5, first_line: float = 0.74,
          line_spacing: float = 1.25):
    paragraph = doc.add_paragraph()
    paragraph.alignment = align
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.line_spacing = line_spacing
    if first_line:
        paragraph.paragraph_format.first_line_indent = Cm(first_line)
    _font(paragraph.add_run(text), size, bold, color)
    return paragraph


def _bottom_border(cell, size: int = 8, color: str = "3A3A3A") -> None:
    """只给单元格加下边框，作为可书写的手写签名线。"""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "0")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def build() -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_height = Cm(29.7)
    section.page_width = Cm(21.0)
    section.top_margin = Cm(1.9)
    section.bottom_margin = Cm(1.3)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    style = doc.styles["Normal"]
    style.font.name = FONT
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(4)
    _font(title.add_run("项目原创性与权属声明"), 20, True)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.paragraph_format.space_after = Pt(10)
    _font(sub.add_run("第五届「未来杯」创新大赛 · 赛道一 Agent 编排创新应用"), 9.5, False, MUTED)

    rows = [
        ("项目名称", "急先锋（Rapid Response OS）· 应急资源调度多智能体编排平台"),
        ("参赛队伍", "OKC"),
        ("队伍成员", "、".join(MEMBERS)),
        ("所在院校", "武汉学院"),
        ("代码仓库", "https://github.com/Jtyang123/jixianfeng"),
    ]
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].width = Cm(3.0)
        cells[1].width = Cm(13.2)
        for cell, text, bold in ((cells[0], key, True), (cells[1], value, False)):
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.space_before = Pt(1)
            _font(paragraph.add_run(text), 9, bold)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    intro = doc.add_paragraph()
    intro.paragraph_format.space_after = Pt(6)
    intro.paragraph_format.line_spacing = 1.35
    _font(intro.add_run("本人作为 OKC 队伍队长，代表本队就上述项目作出如下声明，并对声明内容的真实性承担全部责任："), 10, True)

    clauses = [
        "本项目由 OKC 队伍独立完成，不存在抄袭、代做、购买成品或由第三方机构代写的情形。",
        "提交的源代码、项目白皮书、介绍 PPT、演示视频、界面截图与运行数据，均由本队自行产出；"
        "未使用其他参赛队伍的材料，也未使用任何未经授权的第三方素材。",
        "项目使用的主要开源组件及其许可证见下表。本队遵守各组件许可证条款，未删除或修改其版权与许可声明。",
        "系统使用演练构造数据，不接入真实灾害监测与指挥系统，不具备真实自动指挥能力；"
        "材料中未编造资质、获奖、应用推广或合作证明，未把演练结果表述为真实应用成效。",
        "如经核查存在违反上述声明的情形，本队接受赛事主办方的处理决定，并承担由此产生的相应责任。",
    ]
    for index, text in enumerate(clauses, 1):
        _para(doc, f"{index}. {text}")

    caption = doc.add_paragraph()
    caption.paragraph_format.space_before = Pt(6)
    caption.paragraph_format.space_after = Pt(3)
    _font(caption.add_run("附：主要开源组件与许可证"), 9.5, True)

    grid = doc.add_table(rows=1, cols=3)
    grid.style = "Table Grid"
    grid.alignment = WD_TABLE_ALIGNMENT.CENTER
    header = grid.rows[0].cells
    for cell, text in zip(header, ("组件", "版本", "许可证")):
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(1)
        _font(paragraph.add_run(text), 9, True)
    for name, version, license_name in COMPONENTS:
        cells = grid.add_row().cells
        for cell, text, center in ((cells[0], name, False), (cells[1], version, True), (cells[2], license_name, True)):
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.space_before = Pt(1)
            _font(paragraph.add_run(text), 8.5)

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(3)
    note.paragraph_format.space_after = Pt(2)
    _font(note.add_run("说明：上表为主要运行时依赖；openpyxl、Vite、@lucide/vue 等其余组件为 MIT / Apache-2.0 / ISC 许可，完整版本以 requirements.txt 与 package-lock.json 为准。"), 8, False, MUTED)

    sign = doc.add_paragraph()
    sign.paragraph_format.space_before = Pt(0)
    sign.paragraph_format.space_after = Pt(2)
    _font(sign.add_run("签署人（本人手写）："), 9.5, True)

    # 单人签署：一条 7.6cm 横线，线上签字、线下打印身份标注，不留空白签字位
    columns = [Cm(8.0)]
    sign_table = doc.add_table(rows=2, cols=len(columns))
    sign_table.alignment = WD_TABLE_ALIGNMENT.LEFT
    sign_table.autofit = False
    sign_table.rows[0].height = Cm(1.0)
    sign_table.rows[0].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    for cell, width in zip(sign_table.rows[0].cells, columns):
        cell.width = width
    for cell, width in zip(sign_table.rows[1].cells, columns):
        cell.width = width

    _bottom_border(sign_table.rows[0].cells[0])
    label = sign_table.rows[1].cells[0].paragraphs[0]
    label.alignment = WD_ALIGN_PARAGRAPH.CENTER
    label.paragraph_format.space_before = Pt(1)
    label.paragraph_format.space_after = Pt(0)
    _font(label.add_run(f"{SIGNER}　队长代表本队签署"), 9.5, False, MUTED)

    date = doc.add_paragraph()
    date.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    date.paragraph_format.space_before = Pt(6)
    _font(date.add_run("日期：      年    月    日"), 10)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    doc.save(TARGET)
    return TARGET


if __name__ == "__main__":
    print(f"DOCX: {build()}")
