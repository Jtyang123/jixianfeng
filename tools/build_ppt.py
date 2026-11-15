"""按 2026 未来杯官方模版生成项目讲解 PPT。

模版提供三套视觉：
- 标题幻灯片（layout 0）：深蓝 AI 主视觉，用于封面
- 两栏内容（layout 2）：白底 + 顶部赛事蓝条 + 底部光波，用于全部正文页
- 节标题（layout 3）：深蓝数据流，用于致谢页

正文章节对齐本届提交要求：项目概述 / 解决方案 / 应用成效 / 商业模式 / 团队介绍。
"""

from __future__ import annotations

import struct
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"
PPT_TEMPLATE = Path(r"C:\Users\22103\Desktop\2026未来杯PPT模版.pptx")
TARGET = DOCS / "急先锋_项目讲解PPT.pptx"

# ── 队伍信息：提交前只需改这一段，然后重跑 py tools/build_ppt.py ──────────
TEAM = {
    "name": "OKC",
    "speaker": "【待填】",          # 答辩/演讲人姓名
    "advisor": "【待填】",          # 指导教师
    "school": "【待填】",           # 学校 / 单位
    "repo": "github.com/Jtyang123/jixianfeng",
    "members": ["【待填】", "【待填】", "【待填】", "【待填】"],
}

# 模版主题色（取自模版 theme1.xml）
BLUE = RGBColor(0x48, 0x74, 0xCB)
BLUE_DEEP = RGBColor(0x1B, 0x36, 0x7A)
BLUE_SOFT = RGBColor(0xEE, 0xF3, 0xFD)
ORANGE = RGBColor(0xEE, 0x82, 0x2F)
GOLD = RGBColor(0xF2, 0xBA, 0x02)
GREEN = RGBColor(0x5A, 0xAE, 0x3C)
TEAL = RGBColor(0x25, 0xA9, 0x9C)
RED = RGBColor(0xE5, 0x4C, 0x5E)
INK = RGBColor(0x1A, 0x22, 0x33)
MUTED = RGBColor(0x6B, 0x76, 0x88)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LINE = RGBColor(0xD5, 0xE1, 0xF6)
CARD = RGBColor(0xF7, 0xFA, 0xFF)

FONT = "微软雅黑"

LAYOUT_COVER = 0
LAYOUT_CONTENT = 2
LAYOUT_SECTION = 3


def _write_run(run, text, size, color, bold, align=None, space=None):
    run.text = text
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        node = rpr.find(qn(tag))
        if node is None:
            from pptx.oxml import parse_xml
            from pptx.oxml.ns import nsdecls
            node = parse_xml(f'<{tag} {nsdecls("a")} typeface="{FONT}"/>')
            rpr.append(node)
        else:
            node.set("typeface", FONT)


def text(slide, left, top, width, height, body, size=12, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.15):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    for index, line in enumerate(str(body).split("\n")):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.alignment = align
        para.line_spacing = spacing
        run = para.add_run()
        _write_run(run, line, size, color, bold)
    return box


def rect(slide, left, top, width, height, fill, line=None, rounded=False, radius=0.06,
         transparency=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if transparency is not None:
        shape.fill.fore_color._xFill.srgbClr.append(
            shape.fill.fore_color._xFill.srgbClr.makeelement(
                qn("a:alpha"), {"val": str(int((1 - transparency) * 100000))}
            )
        )
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(0.75)
    if rounded:
        try:
            shape.adjustments[0] = radius
        except Exception:
            pass
    shape.shadow.inherit = False
    return shape


def picture(slide, path, left, top, width, height, trim=(0.0, 0.0, 0.0, 0.0)):
    """trim=(left, top, right, bottom) 以比例裁掉截图自带边框。"""
    if not path.exists():
        return None
    shape = slide.shapes.add_picture(str(path), Inches(left), Inches(top),
                                     width=Inches(width), height=Inches(height))
    head = path.read_bytes()[:32]
    crop_l = crop_t = crop_r = crop_b = 0.0
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        image_ratio, box_ratio = w / max(h, 1), width / max(height, 0.001)
        if image_ratio > box_ratio:
            crop = (1 - box_ratio / image_ratio) / 2
            crop_l = crop_r = crop
        elif image_ratio < box_ratio:
            crop = (1 - image_ratio / box_ratio) / 2
            crop_t = crop_b = crop
    shape.crop_left = min(crop_l + trim[0], 0.49)
    shape.crop_top = min(crop_t + trim[1], 0.49)
    shape.crop_right = min(crop_r + trim[2], 0.49)
    shape.crop_bottom = min(crop_b + trim[3], 0.49)
    return shape


def clear_slides(prs):
    ids = prs.slides._sldIdLst
    for slide_id in list(ids):
        prs.part.drop_rel(slide_id.rId)
        ids.remove(slide_id)


def page_title(slide, chapter, title, sub=None):
    """正文页页眉：章节标识 + 24 号标题（对齐模版字号规范）。"""
    rect(slide, 0.66, 0.98, 0.055, 0.62, BLUE)
    text(slide, 0.82, 0.96, 6.4, 0.24, chapter, 9.5, BLUE, True)
    text(slide, 0.82, 1.19, 11.6, 0.44, title, 21, INK, True)
    if sub:
        text(slide, 0.82, 1.64, 11.6, 0.26, sub, 10, MUTED)


def page_foot(slide, number, note="急先锋 · 应急资源调度多智能体编排平台    演练数据，不替代现场指挥"):
    text(slide, 0.66, 7.06, 8.6, 0.22, note, 8, MUTED)
    text(slide, 12.05, 7.06, 0.62, 0.22, f"{number:02d}", 8, MUTED, True, PP_ALIGN.RIGHT)


def card(slide, left, top, width, height, title, body, accent=BLUE, tag=None,
         title_size=13, body_size=9.5, fill=CARD):
    rect(slide, left, top, width, height, fill, LINE, rounded=True)
    rect(slide, left, top, 0.06, height, accent)
    cursor = top + 0.16
    if tag:
        text(slide, left + 0.22, cursor, width - 0.42, 0.2, tag, 8.5, accent, True)
        cursor += 0.24
    text(slide, left + 0.22, cursor, width - 0.42, 0.3, title, title_size, INK, True)
    text(slide, left + 0.22, cursor + 0.38, width - 0.4, height - (cursor - top) - 0.46,
         body, body_size, MUTED, spacing=1.3)


def dark_card(slide, left, top, width, height, title, body, accent, tag=None):
    rect(slide, left, top, width, height, RGBColor(0x25, 0x45, 0x8E), RGBColor(0x4A, 0x6F, 0xC0),
         rounded=True)
    rect(slide, left, top, 0.06, height, accent)
    cursor = top + 0.18
    if tag:
        text(slide, left + 0.24, cursor, width - 0.44, 0.2, tag, 8.5, accent, True)
        cursor += 0.26
    text(slide, left + 0.24, cursor, width - 0.44, 0.3, title, 13, WHITE, True)
    text(slide, left + 0.24, cursor + 0.38, width - 0.42, height - (cursor - top) - 0.48,
         body, 9.5, RGBColor(0xC9, 0xD8, 0xF2), spacing=1.3)


def build() -> Path:
    prs = Presentation(str(PPT_TEMPLATE))
    clear_slides(prs)
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    cover_layout = prs.slide_layouts[LAYOUT_COVER]
    content = prs.slide_layouts[LAYOUT_CONTENT]
    section = prs.slide_layouts[LAYOUT_SECTION]

    # ── 01 封面（深蓝 AI 主视觉，文字集中在上半部，避开底部 3D 元素）────────
    slide = prs.slides.add_slide(cover_layout)
    text(slide, 0.92, 0.95, 7.0, 0.28, "第五届「未来杯」创新大赛 · 赛道一　Agent 编排创新应用",
         10.5, RGBColor(0xA9, 0xC4, 0xF5), True)
    text(slide, 0.92, 1.42, 8.2, 0.95, "急先锋", 54, WHITE, True)
    text(slide, 0.95, 2.46, 9.6, 0.62, "应急资源调度多智能体编排平台", 27, WHITE, True)
    rect(slide, 0.98, 3.22, 1.55, 0.055, ORANGE)
    text(slide, 0.95, 3.44, 9.8, 0.42, "生成 · 独立核验 · 结构化退回 · 重算 · 人工审批 · 可复现交付",
         13.5, RGBColor(0xBB, 0xCF, 0xF0))
    info = (f"队伍名称：{TEAM['name']}　　演讲人：{TEAM['speaker']}　　"
            f"指导教师：{TEAM['advisor']}　　学校：{TEAM['school']}")
    text(slide, 0.95, 4.28, 10.2, 0.3, info, 13, RGBColor(0x9F, 0xB8, 0xE6), True)
    text(slide, 0.95, 4.78, 10.2, 0.3,
         f"仓库：{TEAM['repo']}   ｜   演示数据均已标识，不替代现场应急指挥", 10,
         RGBColor(0x82, 0x9C, 0xCE))

    # ── 02 目录 ──────────────────────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    text(slide, 1.05, 1.62, 1.5, 0.85, "目", 44, BLUE, True)
    text(slide, 1.05, 2.46, 1.5, 0.85, "录", 44, BLUE, True)
    text(slide, 1.05, 3.52, 2.2, 0.28, "CONTENTS", 10, MUTED, True)
    chapters = [
        ("01", "项目概述", "背景痛点 · 平台定位 · 核心优势"),
        ("02", "解决方案", "五层架构 · 四 Agent · 编排调度 · 独立核验 · 自循环"),
        ("03", "应用成效", "真实运行轨迹 · 六阶段耗时 · 72 样本基准"),
        ("04", "商业模式", "推广路径 · 目标客户 · 生态运作"),
        ("05", "团队介绍", "成员分工 · 工程化能力 · 团队优势"),
    ]
    for index, (num, name, detail) in enumerate(chapters):
        top = 1.58 + index * 1.02
        rect(slide, 3.62, top + 0.06, 0.5, 0.5, BLUE if index % 2 == 0 else BLUE_DEEP, rounded=True)
        text(slide, 3.62, top + 0.15, 0.5, 0.3, num, 11, WHITE, True, PP_ALIGN.CENTER)
        text(slide, 4.32, top, 3.4, 0.36, name, 19, INK, True)
        text(slide, 7.14, top + 0.08, 5.2, 0.3, detail, 10.5, MUTED)
    page_foot(slide, 2)

    # ── 03 01 项目概述：背景与痛点 ────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "01　项目概述", "应急调度不是一次问答，而是高约束协作")
    pains = [
        ("信息不统一", "物资名称、单位、仓库和库存版本各自为政，方案无法跨次复用。", ORANGE),
        ("约束被忽略", "主通道封闭、载重上限与时效要求，往往到执行前才暴露。", RED),
        ("缺少独立核验", "生成方案的角色同时批准方案，确认偏差被直接带入执行。", BLUE),
        ("无法快速重算", "现场条件一变，只能人工重新拉表、电话协调、开会拍板。", TEAL),
    ]
    for index, (name, body, accent) in enumerate(pains):
        left = 0.66 + (index % 2) * 6.13
        top = 2.0 + (index // 2) * 1.92
        card(slide, left, top, 5.83, 1.62, name, body, accent, f"0{index + 1}", 15, 11)
    rect(slide, 0.66, 6.18, 11.96, 0.62, BLUE_SOFT, LINE, rounded=True)
    text(slide, 0.92, 6.32, 11.4, 0.34,
         "核心判断：平台价值不在于生成长报告，而在于让方案经过硬规则核验，并在失败后收敛。",
         12.5, BLUE_DEEP, True)
    page_foot(slide, 3)

    # ── 04 01 项目概述：定位与边界 ────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "01　项目概述", "数据驱动的应急调度推演与预案生成平台")
    rect(slide, 0.66, 1.94, 7.32, 4.05, CARD, LINE, rounded=True)
    text(slide, 0.94, 2.12, 6.6, 0.3, "平台边界：写清楚做什么，也写清楚不做什么", 14, INK, True)
    rows = [
        ("用户上传", "物资库、库存快照、仓库、车辆、路线、事件与需求"),
        ("系统输出", "三套多目标方案、独立核验报告、风险提示、执行清单、可打印报告"),
        ("不承诺", "真实灾害自动发现、真实库存直连、真实车辆自动派遣、替代现场指挥"),
    ]
    for index, (label, body) in enumerate(rows):
        top = 2.62 + index * 1.02
        text(slide, 0.94, top, 1.3, 0.28, label, 11, BLUE, True)
        text(slide, 2.28, top - 0.02, 5.3, 0.86, body, 10.5, MUTED, spacing=1.35)
    card(slide, 8.26, 1.94, 4.36, 1.18, "目标用户", "园区、学校、医院、社区、物流仓储与应急演练机构", TEAL, "WHO")
    card(slide, 8.26, 3.28, 4.36, 1.18, "三种模式", "预案编制　/　场景推演　/　辅助决策", ORANGE, "MODE")
    card(slide, 8.26, 4.62, 4.36, 1.37, "数据边界", "演练数据全程明确标识；生产落地需授权接入真实系统。", MUTED, "DATA")
    text(slide, 0.68, 6.22, 11.9, 0.34,
         "核心优势：四角色权限隔离 + 硬规则独立核验 + 结构化回环收敛 + 固定快照可复现。",
         12, BLUE_DEEP, True)
    page_foot(slide, 4)

    # ── 05 02 解决方案：架构 ─────────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "五层架构 + 三档互斥运行时")
    layers = [
        ("交互层", "Vue 3 工作台", "场景配置 · 地图态势 · 多方案对比 · Planner 轨迹 · 人工审批", BLUE),
        ("服务层", "FastAPI 业务层", "运行 API · SSE 事件流 · 库存快照 · 报告导出 · AgentArts Adapter", BLUE_DEEP),
        ("编排层", "编排与调度层", "ReAct Planner · 并行 DAG · 条件分支 · 确定性推进 · 人工门控", ORANGE),
        ("能力层", "Agent 与工具层", "A01 需求 · A02 匹配 · A03 核验 · A04 交付　｜　RAG · 规则引擎 · 规划器", TEAL),
        ("数据层", "数据与证据层", "PostgreSQL / SQLite 回退 · 物资主数据 · 库存快照 · 运行事件 · 证据引用", MUTED),
    ]
    for index, (tag_name, name, body, accent) in enumerate(layers):
        top = 1.94 + index * 0.9
        rect(slide, 0.8, top, 11.72, 0.78, CARD, LINE, rounded=True)
        rect(slide, 0.8, top, 0.06, 0.78, accent)
        text(slide, 1.04, top + 0.24, 0.66, 0.28, tag_name, 9, accent, True)
        text(slide, 1.8, top + 0.2, 2.5, 0.34, name, 12, INK, True)
        text(slide, 4.4, top + 0.24, 7.9, 0.3, body, 9.5, MUTED)
    text(slide, 0.84, 6.44, 11.8, 0.3,
         "三档互斥：llm-planner（核心本地 + AgentArts 扩展审查）　/　agentarts（四角色云端）　/　local（全本地）",
         9.5, ORANGE, True)
    page_foot(slide, 5)

    # ── 06 02 解决方案：Agent 团队 ───────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "四个角色，四套权限边界")
    agents = [
        ("A01　需求研判", "事件与人口 → 结构化需求\n\n工具：需求估算 / 物资库 / RAG\n\n禁止：分配库存、指定路线", BLUE),
        ("A02　资源匹配", "需求 + 库存 + 路网 → 三套方案\n\n工具：库存 / 车辆 / 路线 / 规划器\n\n禁止：修改库存、自我批准", ORANGE),
        ("A03　独立核验", "候选方案 → 通过 / 退回 / 转人工\n\n工具：库存 / 载重 / 道路 / 时效\n\n禁止：编辑方案、读取生成推理", RED),
        ("A04　方案交付", "已核验方案 → 对比 / 风险 / 报告\n\n工具：比较 / 渲染 / 导出\n\n禁止：改数字、删风险", GREEN),
    ]
    for index, (name, body, accent) in enumerate(agents):
        card(slide, 0.66 + index * 3.1, 1.94, 2.86, 4.12, name, body, accent, f"0{index + 1}", 12.5, 9.5)
    text(slide, 0.7, 6.28, 11.9, 0.34,
         "统一契约：run_id、输入产物、约束条件、数据范围、证据引用、置信度、下一步动作。",
         10.5, MUTED)
    page_foot(slide, 6)

    # ── 07 02 解决方案：编排流程 ─────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "并行入口 · 条件分支 · 核验门控 · 结构化回环")
    nodes = [
        (0.72, "事件确认", ORANGE), (2.42, "需求研判", BLUE), (4.12, "资源匹配", BLUE),
        (5.82, "独立核验", RED), (7.52, "通过？", BLUE_DEEP),
        (9.42, "人工审批", GOLD), (11.12, "方案交付", INK),
    ]
    for left, label, accent in nodes:
        rect(slide, left, 2.34, 1.32, 0.72, accent, rounded=True)
        text(slide, left, 2.55, 1.32, 0.32, label, 10.5, WHITE, True, PP_ALIGN.CENTER)
    for left in (2.06, 3.76, 5.46, 7.16, 8.96, 10.66):
        text(slide, left, 2.5, 0.36, 0.3, "→", 13, MUTED, True, PP_ALIGN.CENTER)
    card(slide, 2.42, 3.62, 2.9, 1.28, "FAIL / REWORK",
         "仅回传问题代码 + 证据 + 修正要求\n两轮未收敛升级人工研判", RED, "LOOP", 12, 9.5)
    text(slide, 5.55, 3.9, 0.6, 0.4, "↩", 24, RED, True)
    text(slide, 5.98, 3.96, 1.9, 0.3, "RETURN TO A02", 9, RED, True)
    card(slide, 9.32, 3.62, 3.1, 1.28, "PASS / APPROVAL",
         "核验通过且人工确认后\n才进入交付与执行准备", GREEN, "GATE", 12, 9.5)
    rect(slide, 0.72, 5.24, 11.72, 0.92, BLUE_SOFT, LINE, rounded=True)
    text(slide, 0.96, 5.42, 11.3, 0.34,
         "并行批次：需求研判与 RAG 规则检索无强依赖，进入同一批次并发执行，压缩首轮等待。",
         10.5, BLUE_DEEP, True)
    text(slide, 0.76, 6.34, 11.8, 0.3,
         "门控原则：核验不通过不交付，人工未确认不执行；每一步状态变化都写入事件流。", 10.5, MUTED)
    page_foot(slide, 7)

    # ── 08 02 解决方案：动态调度 ─────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "能力加权 DAG 调度，而不是固定串行脚本")
    text(slide, 0.8, 1.96, 4.6, 0.3, "复杂度输入", 14, INK, True)
    inputs = ["事件等级与响应时限", "受影响人口与物资种类", "封闭路线数量", "当前库存快照", "可用运力约束"]
    for index, item in enumerate(inputs):
        top = 2.46 + index * 0.66
        rect(slide, 0.8, top, 4.9, 0.52, CARD, LINE, rounded=True)
        text(slide, 1.04, top + 0.14, 4.4, 0.26, f"·　{item}", 10, MUTED)
    rect(slide, 6.05, 1.94, 6.4, 3.9, CARD, LINE, rounded=True)
    text(slide, 6.3, 2.12, 5.8, 0.3, "调度结果（真实运行事件）", 14, INK, True)
    dispatch = [
        ("A01 需求研判", "priority 96", "parallel", BLUE),
        ("RAG 规则检索", "priority 94", "parallel", TEAL),
        ("A02 资源匹配", "dynamic", "capability-weighted", ORANGE),
        ("A03 独立核验", "priority 99", "gated", RED),
        ("A04 方案交付", "post-approval", "serial", GREEN),
    ]
    for index, (name, prio, mode, accent) in enumerate(dispatch):
        top = 2.58 + index * 0.6
        rect(slide, 6.3, top + 0.1, 0.05, 0.24, accent)
        text(slide, 6.48, top, 2.2, 0.28, name, 10, INK, True)
        text(slide, 8.72, top + 0.02, 1.3, 0.26, prio, 9, accent, True)
        text(slide, 10.1, top + 0.02, 2.1, 0.26, mode, 9, MUTED)
    text(slide, 0.82, 6.28, 11.8, 0.34,
         "调度器按能力画像、历史成功率与当前负载打分排序，并把每次分配写入可审计事件。",
         10.5, BLUE_DEEP, True)
    page_foot(slide, 8)

    # ── 09 02 解决方案：独立核验 ─────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "生成者不批准，核验者不修改")
    card(slide, 0.68, 1.94, 3.72, 3.96, "隔离上下文",
         "核验只读取原始需求、候选方案、\n库存快照、道路状态与规则集。\n\n"
         "不读取生成角色的内部推理。\n\n没有编辑候选方案的权限。", RED, "ISOLATE", 14, 10.5)
    checks = [
        ("道路封闭", "主通道 / 备用通道可用性"),
        ("库存约束", "可用量 − 已锁定量"),
        ("运力约束", "物资重量 / 车辆载重"),
        ("时效约束", "预计到达 / 要求时限"),
        ("需求覆盖", "已分配 / 总需求"),
    ]
    for index, (name, detail) in enumerate(checks):
        top = 1.94 + index * 0.76
        rect(slide, 4.72, top, 7.72, 0.66, CARD, LINE, rounded=True)
        text(slide, 4.96, top + 0.18, 1.9, 0.28, name, 11, INK, True)
        text(slide, 7.06, top + 0.19, 5.1, 0.26, detail, 9.5, MUTED)
    text(slide, 4.78, 5.92, 7.6, 0.3,
         "通过门槛：无硬阻断且评分 ≥ 80；否则退回重算或升级人工研判。", 10.5, ORANGE, True)
    text(slide, 0.72, 6.24, 11.8, 0.34,
         "7 类问题代码：道路阻断、库存不足、载重超限、时效违约、需求缺口、数据过期、方案冲突。",
         10, MUTED)
    page_foot(slide, 9)

    # ── 10 02 解决方案：自循环 ───────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "PDCA 自循环：生成 → 验证 → 退回 → 修正 → 再验证")
    steps = [
        ("01", "生成", "A02 产出三套候选方案", BLUE),
        ("02", "验证", "A03 发现 7 类硬阻断", RED),
        ("03", "反馈", "只回传问题代码与证据", ORANGE),
        ("04", "重算", "排除封闭路线，改走备用通道", TEAL),
        ("05", "收敛", "三套方案通过，覆盖率达 100%", GREEN),
    ]
    for index, (num, name, body, accent) in enumerate(steps):
        card(slide, 0.5 + index * 2.5, 1.96, 2.3, 1.86, name, body, accent, num, 13, 9.5)
    rect(slide, 0.66, 4.08, 11.96, 1.42, CARD, LINE, rounded=True)
    text(slide, 0.94, 4.26, 1.8, 0.3, "量化结果", 13, INK, True)
    text(slide, 2.72, 4.24, 9.6, 0.62,
         "首轮评分 0 → 最终 100　　不安全方案拦截 3/3　　最终需求覆盖 100%", 12.5, BLUE_DEEP, True)
    text(slide, 2.72, 4.86, 9.6, 0.42,
         "同一输入重复运行结果一致，重算只重跑必要步骤，不整份推倒。", 10.5, MUTED)
    text(slide, 0.7, 5.68, 11.8, 0.3,
         "回环不是兜底话术：退回内容结构化（问题代码 + 证据 + 修正要求），可直接驱动下一步重算。",
         10.5, ORANGE, True)
    text(slide, 0.7, 6.24, 11.8, 0.3,
         "实测：首轮因主通道封闭被独立核验退回，第二轮改走备用通道后通过，评分恢复 100。", 10, MUTED)
    page_foot(slide, 10)

    # ── 11 02 解决方案：技术底座 ─────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "02　解决方案", "LLM 负责规划，工具负责计算，规则负责放行")
    card(slide, 0.68, 1.94, 3.86, 4.04, "RAG 知识库",
         "领域同义词 + BM25 + 哈希词袋\n\n离线词法检索，证据编号可追溯\n\n6 条规则文档（道路、库存、\n载重、时效、优先级、核验）\n\n"
         "embedding 后端可插拔：\n配置 EMBEDDING_* 即切远程向量", TEAL, "RAG", 14, 10)
    card(slide, 4.79, 1.94, 3.86, 4.04, "LLM / AgentArts",
         "需求理解与结构化\n\n策略权衡解释、根因归纳\n\n执行摘要与报告文本\n\n"
         "华为云 AgentArts 四角色\n运行时可直接替换\n\n超时自动回退确定性基线", ORANGE, "LLM", 14, 10)
    card(slide, 8.9, 1.94, 3.86, 4.04, "工具与协议",
         "inventory_query\nroute_query\nvehicle_query\nmulti_objective_planner\nrule_engine\nrag_retrieve\nexport_pdf\n\n"
         "统一注册与鉴权，\n调用留痕可审计", BLUE, "TOOL", 14, 10)
    text(slide, 0.72, 6.24, 11.8, 0.34,
         "边界：LLM 不直接改数字。数量、路线、载重与库存全部由确定性工具计算，模型只做判断与解释。",
         10.5, BLUE_DEEP, True)
    page_foot(slide, 11)

    # ── 12 03 应用成效：真实轨迹 ─────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "03　应用成效", "Planner 真实轨迹：8 步计划 · 10 步执行 · 1 次重规划")
    picture(slide, SHOTS / "ppt_planner.png", 0.66, 1.88, 12.0, 4.32, trim=(0.021, 0.0, 0.0, 0.0))
    metrics = [("10", "实际执行步骤", BLUE), ("1", "触发重规划", ORANGE),
               ("5", "LLM 决策次数", TEAL), ("8", "初始计划步骤", MUTED)]
    for index, (value, label, accent) in enumerate(metrics):
        left = 0.66 + index * 3.02
        text(slide, left, 6.34, 0.72, 0.34, value, 17, accent, True)
        text(slide, left + 0.42, 6.44, 2.4, 0.24, label, 9.5, MUTED)
    page_foot(slide, 12, "急先锋 · Planner 轨迹面板    演练数据，不替代现场指挥")

    # ── 13 03 应用成效：阶段耗时与基准 ───────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "03　应用成效", "六阶段耗时可归因，基准可复现")
    picture(slide, SHOTS / "ppt_stage.png", 0.66, 1.9, 7.55, 4.15, trim=(0.0, 0.035, 0.0, 0.0))
    evidence = [
        ("72", "样本基准", "6 场景 × 3 重复 × 4 并发档", BLUE),
        ("100%", "安全结果率", "单 Agent 贪心基线 66.67%", GREEN),
        ("72.67", "多 Agent 平均评分", "基线 66.67，提升 +6.0", ORANGE),
        ("137 / 7", "后端 / 前端测试", "全部通过，PG 项 3 项跳过", TEAL),
    ]
    for index, (value, label, detail, accent) in enumerate(evidence):
        top = 1.92 + index * 1.08
        rect(slide, 8.46, top, 4.16, 0.96, CARD, LINE, rounded=True)
        rect(slide, 8.46, top, 0.06, 0.96, accent)
        text(slide, 8.7, top + 0.1, 1.5, 0.36, value, 15.5, accent, True)
        text(slide, 10.14, top + 0.1, 2.3, 0.26, label, 10, INK, True)
        text(slide, 10.14, top + 0.42, 2.34, 0.42, detail, 8.5, MUTED, spacing=1.2)
    text(slide, 0.68, 6.32, 7.6, 0.3, "阶段耗时覆盖工具执行与对应决策，编排开销单独记账。", 9.5, MUTED)
    text(slide, 8.5, 6.32, 4.2, 0.3, "基准 scope：确定性核心，不含 LLM 与 IO。", 9, ORANGE, True)
    page_foot(slide, 13)

    # ── 14 04 商业模式 ──────────────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "04　商业模式", "先做深演练场景，再做宽行业模板")
    text(slide, 0.7, 1.92, 4.4, 0.3, "推广路径：三步走", 13, INK, True)
    stages = [
        ("第一步", "校内与园区演练验证", "免费试点换真实场景与规则，沉淀测评数据。", BLUE),
        ("第二步", "行业客户付费试点", "医院、物流仓储、大型园区按年订阅演练与预案服务。", ORANGE),
        ("第三步", "行业模板与授权", "场景 Agent、核验规则和报告模板按行业授权复制。", GREEN),
    ]
    for index, (tag_name, name, body, accent) in enumerate(stages):
        top = 2.34 + index * 1.24
        rect(slide, 0.7, top, 6.0, 1.1, CARD, LINE, rounded=True)
        rect(slide, 0.7, top, 0.06, 1.1, accent)
        text(slide, 0.94, top + 0.12, 1.1, 0.24, tag_name, 8.5, accent, True)
        text(slide, 1.96, top + 0.1, 4.5, 0.28, name, 11.5, INK, True)
        text(slide, 1.96, top + 0.44, 4.5, 0.5, body, 9, MUTED, spacing=1.25)
    text(slide, 7.06, 1.92, 5.5, 0.3, "目标客户与采购场景", 13, INK, True)
    clients = [
        ("应急管理部门", "预案编制与桌面推演"),
        ("园区 / 校园 / 医院", "后勤保障与疏散演练"),
        ("物流与仓储企业", "调拨路径与运力测算"),
        ("演练服务机构", "第三方评估与报告交付"),
    ]
    for index, (name, detail) in enumerate(clients):
        top = 2.34 + index * 0.64
        rect(slide, 7.06, top, 5.56, 0.54, CARD, LINE, rounded=True)
        text(slide, 7.28, top + 0.14, 2.4, 0.26, name, 10, INK, True)
        text(slide, 9.66, top + 0.15, 2.8, 0.24, detail, 9, MUTED)
    rect(slide, 7.06, 4.94, 5.56, 1.06, BLUE_SOFT, LINE, rounded=True)
    text(slide, 7.28, 5.06, 5.1, 0.26, "生态运作", 10.5, BLUE_DEEP, True)
    text(slide, 7.28, 5.36, 5.14, 0.56,
         "华为云 AgentArts 提供云端 Agent 运行时；开源工具链保证可复现；\n与行业机构共建核验规则库，规则即资产。",
         8.8, MUTED, spacing=1.3)
    text(slide, 0.72, 6.28, 11.8, 0.34,
         "收入结构规划：软件订阅 + 演练服务 + 定制实施。现阶段不对外承诺规模化营收。", 10, MUTED)
    page_foot(slide, 14)

    # ── 15 05 团队介绍 ──────────────────────────────────────────────────
    slide = prs.slides.add_slide(content)
    page_title(slide, "05　团队介绍", "四人分工覆盖全链路，工程化能力可验证")
    members = [
        (f"队长{TEAM['members'][0]}", "编排引擎与调度", "负责 Planner、DAG 调度、\n核验门控与整体架构。", BLUE),
        (f"成员{TEAM['members'][1]}", "后端与算法", "工具层、库存原子扣减、\nRAG 检索与基准评测。", ORANGE),
        (f"成员{TEAM['members'][2]}", "前端与可视化", "工作台、地图态势、\n轨迹面板与阶段耗时。", TEAL),
        (f"成员{TEAM['members'][3]}", "测试与文档", "137 项后端测试、CI、\n方案书与演示材料。", GREEN),
    ]
    for index, (name, role, body, accent) in enumerate(members):
        card(slide, 0.66 + index * 3.1, 1.94, 2.86, 2.16, name, role + "\n\n" + body, accent,
             None, 12.5, 10)
    text(slide, 0.7, 4.38, 6.0, 0.3, "团队优势", 13, INK, True)
    strengths = [
        ("可验证", "后端 137 项、前端 7 项测试全绿，CI 可复跑。"),
        ("可复现", "固定库存快照与运行事件，历史方案可对照重放。"),
        ("守边界", "演练数据明确标识，不宣称真实自动指挥。"),
    ]
    for index, (name, body) in enumerate(strengths):
        top = 4.82 + index * 0.62
        rect(slide, 0.7, top, 11.7, 0.52, CARD, LINE, rounded=True)
        rect(slide, 0.7, top, 0.05, 0.52, BLUE)
        text(slide, 0.94, top + 0.14, 1.5, 0.26, name, 10, BLUE, True)
        text(slide, 2.3, top + 0.15, 10.0, 0.24, body, 9.5, MUTED)
    text(slide, 0.74, 6.62, 11.6, 0.26,
         "注：姓名、学校与指导教师信息由团队在提交前补齐，本节仅呈现分工与能力结构。", 8.5, ORANGE)
    page_foot(slide, 15)

    # ── 16 致谢（深蓝数据流）─────────────────────────────────────────────
    slide = prs.slides.add_slide(section)
    text(slide, 0.92, 2.34, 6.0, 1.0, "谢谢观看", 44, WHITE, True)
    text(slide, 0.95, 3.42, 6.0, 0.42, "Thanks for watching", 16, RGBColor(0x9F, 0xB8, 0xE6))
    rect(slide, 0.98, 4.12, 1.55, 0.055, ORANGE)
    text(slide, 0.95, 4.38, 9.6, 0.34, "急先锋 · 应急资源调度多智能体编排平台", 13, WHITE, True)
    text(slide, 0.95, 4.82, 10.4, 0.34,
         "生成 · 独立核验 · 结构化退回 · 重算 · 人工审批 · 可复现交付", 11,
         RGBColor(0xA9, 0xC4, 0xF5))
    text(slide, 0.95, 6.42, 10.4, 0.3, "演示数据，不替代现场应急指挥。", 9, RGBColor(0x7B, 0x95, 0xC7))

    prs.save(TARGET)
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"PPTX: {path}")
