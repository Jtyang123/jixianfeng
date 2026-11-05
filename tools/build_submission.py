from __future__ import annotations

import html
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"

PAPER = RGBColor(246, 243, 235)
INK = RGBColor(20, 42, 40)
TEAL = RGBColor(23, 117, 110)
ORANGE = RGBColor(230, 91, 53)
LIME = RGBColor(198, 229, 161)
MUTED = RGBColor(110, 124, 118)
WHITE = RGBColor(255, 253, 248)
LINE = RGBColor(222, 216, 203)
RED = RGBColor(198, 67, 50)


def markdown_to_html(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    in_list = False
    in_ol = False
    in_code = False
    paragraph: list[str] = []

    def inline(value: str) -> str:
        value = html.escape(value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        return value

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            output.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_lists():
        nonlocal in_list, in_ol
        if in_list:
            output.append("</ul>")
            in_list = False
        if in_ol:
            output.append("</ol>")
            in_ol = False

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_paragraph(); close_lists()
            output.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            output.append(html.escape(line))
            continue
        if not line.strip():
            flush_paragraph(); close_lists()
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            flush_paragraph(); close_lists()
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", line)
        if ordered:
            flush_paragraph()
            if in_list:
                output.append("</ul>"); in_list = False
            if not in_ol:
                output.append("<ol>"); in_ol = True
            output.append(f"<li>{inline(ordered.group(1))}</li>")
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            flush_paragraph()
            if in_ol:
                output.append("</ol>"); in_ol = False
            if not in_list:
                output.append("<ul>"); in_list = True
            output.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        if in_list or in_ol:
            close_lists()
        paragraph.append(line)
    flush_paragraph(); close_lists()
    if in_code:
        output.append("</pre>")
    return "\n".join(output)


def build_document_html() -> Path:
    source = (DOCS / "方案书.md").read_text(encoding="utf-8").lstrip("\ufeff")
    body = markdown_to_html(source)
    body = re.sub(r"^<h1>.*?</h1>", "", body, count=1, flags=re.S)
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>急先锋项目方案书</title>
<style>
@page {{ size: A4; margin: 18mm 18mm 20mm; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; color:#172b2a; background:#fffdf8; font-family:'Microsoft YaHei','Noto Sans SC',sans-serif; font-size:10.5pt; line-height:1.8; }}
.cover {{ min-height: 235mm; padding: 30mm 12mm 20mm; page-break-after: always; background: linear-gradient(145deg,#102322 0%,#173b37 70%,#2f5849 100%); color:#f4f3ea; }}
.cover .tag {{ color:#c6e5a1; font:600 11pt 'Consolas',monospace; letter-spacing:.22em; }}
.cover h1 {{ margin-top: .35em; color:#fffdf8; font-size:35pt; line-height:1.2; border:0; }}
.cover .subtitle {{ margin-top:16mm; max-width:130mm; color:#b9ccc5; font-size:14pt; line-height:1.7; }}
.cover .rule {{ width:45mm; height:5px; margin:18mm 0; background:#e65b35; }}
.cover .meta {{ margin-top:28mm; color:#9fb5ad; font-size:10pt; line-height:2; }}
.cover .module {{ margin-top:20mm; display:flex; gap:8mm; color:#d6e5df; font-size:9pt; }}
.content {{ padding: 0 5mm; }}
h1 {{ color:#172b2a; font-size:24pt; border-bottom:4px solid #e65b35; padding-bottom:8px; page-break-after:avoid; }}
h2 {{ margin-top:14mm; color:#172b2a; font-size:17pt; border-left:5px solid #17756e; padding-left:9px; page-break-after:avoid; }}
h3 {{ margin-top:9mm; color:#1e514b; font-size:13pt; page-break-after:avoid; }}
h4 {{ color:#5d5e56; font-size:11pt; page-break-after:avoid; }}
p {{ margin: 4mm 0; text-align:justify; }}
ul,ol {{ margin: 3mm 0 5mm; padding-left: 7mm; }}
li {{ margin: 1.2mm 0; }}
strong {{ color:#163c38; }}
code {{ padding:1px 4px; color:#1e6d66; background:#e9f2ed; border-radius:3px; font-family:Consolas,monospace; }}
pre {{ padding:5mm; color:#d8e9de; background:#102322; border-radius:5px; white-space:pre-wrap; font:9pt/1.6 Consolas,'Microsoft YaHei',monospace; }}
table {{ width:100%; border-collapse:collapse; margin:5mm 0; font-size:9pt; }}
th,td {{ border:1px solid #d8d2c6; padding:2.4mm; text-align:left; vertical-align:top; }}
th {{ background:#e9f2ed; }}
h2 {{ page-break-before: auto; }}
</style></head><body>
<div class="cover">
  <div class="tag">FUTURE CUP / AGENT ORCHESTRATION</div>
  <h1>急先锋<br>应急资源调度<br>多智能体编排平台</h1>
  <div class="rule"></div>
  <div class="subtitle">生成 · 独立核验 · 结构化退回 · 重算 · 人工审批 · 可复现交付</div>
  <div class="meta">第五届“未来杯”创新大赛 · 赛道一 Agent 编排创新应用<br>技术方案书 V1.0 · 2026年11月<br>数据模式：SIMULATED / 演练数据</div>
  <div class="module"><span>Agent 定义</span><span>工作流编排</span><span>动态调度</span><span>结果验证</span><span>自我循环</span></div>
</div>
<div class="content">{body}</div>
</body></html>"""
    target = DOCS / "方案书.html"
    target.write_text(document, encoding="utf-8")
    return target


def add_text(slide, left, top, width, height, text, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT, font="Microsoft YaHei"):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_rect(slide, left, top, width, height, fill, line=None, rounded=False, radius=0.08):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid(); shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line or fill
    if rounded:
        try: shape.adjustments[0] = radius
        except Exception: pass
    return shape


def add_title(slide, kicker, title, dark=False):
    color = WHITE if dark else INK
    add_text(slide, .65, .34, 9.8, .28, kicker, 10, LIME if dark else MUTED, True, font="Consolas")
    add_text(slide, .65, .65, 11.9, .55, title, 26, color, True)


def add_footer(slide, number):
    add_text(slide, .65, 7.12, 5.6, .2, "急先锋 · RAPID RESPONSE OS  /  演练数据", 8, MUTED)
    add_text(slide, 12.2, 7.12, .4, .2, f"{number:02d}", 8, MUTED, align=PP_ALIGN.RIGHT, font="Consolas")


def add_card(slide, left, top, width, height, title, body, accent=TEAL, number=None):
    add_rect(slide, left, top, width, height, WHITE, LINE, rounded=True)
    add_rect(slide, left, top, .07, height, accent)
    if number:
        add_text(slide, left + .22, top + .18, .5, .25, number, 9, accent, True, font="Consolas")
    add_text(slide, left + .24, top + .5, width - .45, .35, title, 13, INK, True)
    add_text(slide, left + .24, top + .93, width - .45, height - 1.1, body, 9.5, MUTED)


def add_picture(slide, path, left, top, width, height):
    if path.exists():
        slide.shapes.add_picture(str(path), Inches(left), Inches(top), width=Inches(width), height=Inches(height))


def build_ppt() -> Path:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 Cover
    slide = prs.slides.add_slide(blank)
    add_rect(slide, 0, 0, 13.333, 7.5, INK)
    add_rect(slide, 9.3, -1.2, 5.3, 5.3, RGBColor(33, 76, 67), rounded=True)
    add_text(slide, .8, .7, 6, .3, "FUTURE CUP / AGENT ORCHESTRATION", 11, LIME, True, font="Consolas")
    add_text(slide, .8, 1.45, 9.4, 1.75, "急先锋", 52, WHITE, True)
    add_text(slide, .8, 3.12, 9.8, .75, "应急资源调度多智能体编排平台", 28, WHITE, True)
    add_rect(slide, .82, 4.2, 1.7, .06, ORANGE)
    add_text(slide, .8, 4.58, 9.6, .65, "生成 · 独立核验 · 结构化退回 · 重算 · 人工审批 · 可复现交付", 14, RGBColor(190, 208, 201))
    add_text(slide, .8, 6.5, 10, .35, "第五届“未来杯”创新大赛 · 赛道一 Agent 编排创新应用", 10, RGBColor(148, 170, 162))
    add_text(slide, 11.1, 6.45, 1.3, .3, "SIMULATED", 9, LIME, True, font="Consolas")

    # 2 Problem
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "01 / PROBLEM", "应急调度不是一次问答，而是高约束协作")
    problems = [
        ("信息不统一", "物资名称、单位、仓库和库存版本混乱，导致方案无法复用。"),
        ("约束被忽略", "主通道封闭、载重和时效问题常常在执行前才暴露。"),
        ("缺少独立核验", "生成者同时批准方案，容易形成确认偏差和错误传播。"),
        ("无法快速重算", "现场条件变化后依赖人工重新拉表、电话协调和开会。"),
    ]
    for i, (title, body) in enumerate(problems):
        add_card(slide, .72 + (i % 2) * 6.05, 1.65 + (i // 2) * 2.25, 5.7, 1.82, title, body, ORANGE if i in (1, 3) else TEAL, f"0{i+1}")
    add_text(slide, .78, 6.55, 11.8, .35, "核心判断：平台价值不在于生成长报告，而在于让方案经过硬规则核验并在失败后收敛。", 12, INK, True)
    add_footer(slide, 2)

    # 3 Positioning
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "02 / POSITIONING", "数据驱动的应急调度推演与预案生成平台")
    add_rect(slide, .75, 1.55, 7.2, 4.9, WHITE, LINE, rounded=True)
    add_text(slide, 1.0, 1.82, 6.4, .4, "平台边界", 17, INK, True)
    add_text(slide, 1.0, 2.35, 6.5, 3.4, "用户上传：物资库、库存快照、仓库、车辆、路线、事件与需求\n\n系统输出：三套多目标方案、独立核验报告、风险提示、执行清单与可打印报告\n\n不承诺：真实灾害自动发现、真实库存直连、真实车辆自动派遣、替代现场指挥", 12, MUTED)
    add_card(slide, 8.35, 1.55, 4.25, 1.5, "目标用户", "园区、学校、医院、社区、物流仓储与应急演练机构", TEAL, "WHO")
    add_card(slide, 8.35, 3.28, 4.25, 1.5, "三种模式", "预案编制 / 场景推演 / 辅助决策", ORANGE, "MODE")
    add_card(slide, 8.35, 5.01, 4.25, 1.45, "数据边界", "演练数据明确标识，生产落地需授权接入真实系统", MUTED, "DATA")
    add_footer(slide, 3)

    # 4 Architecture
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "03 / ARCHITECTURE", "五层架构：交互、API、编排、Agent/工具、数据")
    layers = [
        ("Vue 3 工作台", "场景配置 · 地图态势 · 多方案对比 · Agent 轨迹 · 人工审批", TEAL),
        ("FastAPI 业务层", "运行 API · SSE 事件流 · 库存快照 · 报告导出 · AgentArts Adapter", INK),
        ("编排与调度层", "状态机 · 并行 DAG · 条件分支 · 两轮 Self-Loop · 人工门控", ORANGE),
        ("Agent 与工具层", "A01 需求 · A02 匹配 · A03 核验 · A04 交付 / RAG · 规则引擎 · 规划器", RGBColor(83, 107, 72)),
        ("数据层", "PostgreSQL / SQLite fallback · 物资主数据 · 库存快照 · 路线 · 运行事件 · 证据引用", MUTED),
    ]
    for i, (title, body, color) in enumerate(layers):
        top = 1.43 + i * 1.04
        add_rect(slide, 1.0, top, 11.3, .8, WHITE, LINE, rounded=True)
        add_rect(slide, 1.0, top, .08, .8, color)
        add_text(slide, 1.25, top + .09, 2.5, .3, title, 12, INK, True)
        add_text(slide, 3.75, top + .07, 8.2, .5, body, 10, MUTED)
    add_text(slide, 1.02, 6.9, 11.2, .3, "设计原则：关键数字由工具计算，LLM 负责理解与解释，规则负责否决，人类负责最终责任。", 10, ORANGE, True)
    add_footer(slide, 4)

    # 5 Agents
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "04 / AGENT TEAM", "四个角色，四套权限边界")
    agents = [
        ("A01 需求研判", "事件与人口 → 结构化需求\n工具：需求估算 / 物资库 / RAG\n禁止：分配库存、指定路线", TEAL),
        ("A02 资源匹配", "需求 + 库存 + 路网 → 三套方案\n工具：库存 / 车辆 / 路线 / 规划器\n禁止：修改库存、自我批准", ORANGE),
        ("A03 独立核验", "候选方案 → 通过 / 退回 / 转人工\n工具：库存 / 载重 / 道路 / 时效\n禁止：编辑方案、读取生成推理", RED),
        ("A04 方案交付", "已核验方案 → 对比 / 风险 / 报告\n工具：比较 / 渲染 / 导出\n禁止：改数字、删风险", RGBColor(83, 107, 72)),
    ]
    for i, (title, body, color) in enumerate(agents):
        add_card(slide, .72 + i * 3.13, 1.55, 2.88, 4.6, title, body, color, f"0{i+1}")
    add_text(slide, .76, 6.45, 11.8, .35, "统一契约：run_id、输入产物、约束、数据范围、证据引用、置信度、下一步动作。", 10, MUTED)
    add_footer(slide, 5)

    # 6 Workflow
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "05 / WORKFLOW", "并行入口 · 条件分支 · 核验门控 · 结构化回环")
    nodes = [
        (.9, "事件确认", ORANGE), (2.55, "需求研判", TEAL), (4.2, "资源匹配", TEAL),
        (5.85, "独立核验", RED), (7.5, "通过？", RGBColor(97, 77, 143)),
        (9.35, "人工审批", RGBColor(117, 101, 57)), (11.0, "方案交付", INK),
    ]
    for left, label, color in nodes:
        add_rect(slide, left, 2.5, 1.25, .72, color, color, rounded=True)
        add_text(slide, left + .04, 2.63, 1.17, .35, label, 10, WHITE if color != LIME else INK, True, align=PP_ALIGN.CENTER)
    for left in [2.16, 3.8, 5.45, 7.1, 8.95, 10.6]:
        add_text(slide, left, 2.62, .35, .3, "→", 14, MUTED, True, align=PP_ALIGN.CENTER)
    add_card(slide, 2.55, 4.0, 2.7, 1.3, "FAIL / REWORK", "问题代码 + 证据 + 修正要求\n最多两轮后升级人工研判", RED, "LOOP")
    add_text(slide, 5.4, 4.3, .7, .3, "↩", 26, RED, True)
    add_text(slide, 5.75, 4.25, 2.5, .5, "RETURN TO A02", 9, RED, True, font="Consolas")
    add_card(slide, 9.3, 4.0, 2.95, 1.3, "PASS / APPROVAL", "只有核验通过且人工确认\n才进入交付和执行准备", TEAL, "GATE")
    add_card(slide, .9, 5.7, 11.9, .8, "并行分支", "需求研判与 RAG 规则检索无强依赖，进入同一并行批次，减少首轮等待时间。", TEAL, "PARALLEL")
    add_footer(slide, 6)

    # 7 Scheduling
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "06 / SCHEDULER", "能力加权 DAG 调度，而不是固定串行脚本")
    add_text(slide, .8, 1.55, 5.7, .35, "复杂度输入", 15, INK, True)
    add_text(slide, .8, 2.05, 5.6, 3.8, "事件等级\n物资种类数\n受影响人口\n封闭路线数量\n当前数据快照与运力约束", 13, MUTED)
    add_rect(slide, 6.55, 1.55, 5.95, 4.9, WHITE, LINE, rounded=True)
    add_text(slide, 6.85, 1.8, 5.2, .35, "调度结果", 15, INK, True)
    add_text(slide, 6.85, 2.35, 5.2, 3.5, "A01 需求研判      priority 96 / parallel\nRAG 规则检索     priority 94 / parallel\nA02 资源匹配      dynamic priority\nA03 独立核验      priority 99 / gated\nA04 方案交付      post-approval", 11, TEAL, font="Consolas")
    add_text(slide, .8, 6.55, 11.8, .35, "输出 complexity_score、parallel_branch、loop policy 和 rationale；调度决策进入运行事件流。", 10, ORANGE, True)
    add_footer(slide, 7)

    # 8 Verification
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "07 / VERIFICATION", "生成者不批准，核验者不修改")
    add_card(slide, .75, 1.55, 3.75, 4.8, "隔离上下文", "核验仅读取原始需求、候选方案、库存快照、道路和规则。\n\n不读取生成角色的内部推理。\n\n没有编辑候选方案的权限。", TEAL, "ISOLATE")
    checks = [("道路封闭", "主通道 / 备用通道"), ("库存约束", "可用量 - 锁定量"), ("运力约束", "重量 / 车辆载重"), ("时效约束", "ETA / deadline"), ("需求覆盖", "已分配 / 总需求")]
    for i, (name, detail) in enumerate(checks):
        y = 1.55 + i * .9
        add_rect(slide, 4.9, y, 7.4, .72, WHITE, LINE, rounded=True)
        add_text(slide, 5.15, y + .1, 2.1, .3, name, 11, INK, True)
        add_text(slide, 7.25, y + .11, 4.6, .3, detail, 9, MUTED)
    add_text(slide, 4.95, 6.3, 7.2, .35, "通过门槛：无硬阻断且评分 ≥ 80；否则 rework 或 human_escalation。", 10, ORANGE, True)
    add_footer(slide, 8)

    # 9 Self-loop + metrics
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "08 / SELF-LOOP", "PDCA：生成 → 验证 → 退回 → 修正 → 再验证")
    steps = [("01", "生成", "A02 生成三套候选方案", TEAL), ("02", "验证", "A03 发现 7 类硬阻断", RED), ("03", "反馈", "仅回传问题代码与证据", ORANGE), ("04", "重算", "排除封闭路线，改走备用通道", TEAL), ("05", "收敛", "三套方案通过，覆盖率 100%", RGBColor(83, 107, 72))]
    for i, (num, title, body, color) in enumerate(steps):
        add_card(slide, .55 + i * 2.5, 1.6, 2.3, 2.15, title, body, color, num)
    add_rect(slide, .75, 4.35, 11.9, 1.55, WHITE, LINE, rounded=True)
    add_text(slide, 1.0, 4.55, 2.0, .35, "量化结果", 14, INK, True)
    add_text(slide, 3.0, 4.48, 9.3, .9, "首轮评分 0 → 最终 100    评分提升 +100\n不安全方案拦截 3/3    最终需求覆盖 100%    重复计算一致", 13, TEAL, True, font="Microsoft YaHei")
    add_footer(slide, 9)

    # 10 RAG + LLM + tools
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "09 / AI & TOOLS", "LLM 理解与解释，工具负责事实，规则负责放行")
    add_card(slide, .75, 1.55, 3.8, 4.85, "RAG 知识库", "需求测算\n汛期道路封闭\n多仓库存分配\n车辆载重\nPDCA 核验\n交付字段规范\n\n6 条规则文档 · 证据编号可追溯", TEAL, "RAG")
    add_card(slide, 4.78, 1.55, 3.8, 4.85, "LLM / AgentArts", "需求理解\n策略权衡解释\n核验根因归纳\n执行摘要生成\n\nAgentArts Adapter\n本地确定性回退", ORANGE, "LLM")
    add_card(slide, 8.8, 1.55, 3.8, 4.85, "工具与协议", "inventory_query\nroute_query\nmulti_objective_planner\nrule_engine\nrag_retrieve\nexport_pdf\n\nFunction Calling / MCP 风格注册", RGBColor(83, 107, 72), "TOOL")
    add_footer(slide, 10)

    # 11 Data & materials
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "10 / DATA PRODUCT", "物资库与库存快照构成可复用数据底座")
    add_picture(slide, SHOTS / "v2_materials.png", .65, 1.45, 6.0, 3.75)
    add_picture(slide, SHOTS / "v2_data.png", 6.9, 1.45, 6.0, 3.75)
    add_text(slide, .8, 5.45, 5.8, .8, "物资主数据：编码、别名、规格、单位、换算、储存、保质期、冷链、危险品、优先级", 10, MUTED)
    add_text(slide, 7.0, 5.45, 5.8, .8, "库存快照：每次上传独立版本，推演固定引用，历史方案可复现", 10, MUTED)
    add_footer(slide, 11)

    # 12 Product screenshot
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "11 / PRODUCT DEMO", "工作台：地图、方案、Agent 运行轨同屏联动")
    add_picture(slide, SHOTS / "v2_workbench.png", .65, 1.42, 12.05, 5.45)
    add_footer(slide, 12)

    # 13 Orchestration screenshot
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "12 / ORCHESTRATION", "五个核心模块在平台内可视化")
    add_picture(slide, SHOTS / "v2_orchestration.png", .65, 1.42, 12.05, 5.45)
    add_footer(slide, 13)

    # 14 Evaluation
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, PAPER)
    add_title(slide, "13 / EVALUATION", "可量化、可复现、可解释的结果验证")
    add_picture(slide, SHOTS / "v2_evaluation.png", .65, 1.42, 7.65, 5.45)
    add_card(slide, 8.65, 1.55, 3.95, 1.15, "匹配赛题评分", "功能完整性 · 技术可靠性\nAI 合理性 · 文档质量", TEAL, "25×4")
    add_card(slide, 8.65, 2.92, 3.95, 1.15, "量化证据", "3/3 不安全方案拦截\n评分 +100 · 覆盖率 100%", ORANGE, "BENCH")
    add_card(slide, 8.65, 4.29, 3.95, 1.15, "可复现证据", "固定快照 · 重复计算一致\npytest 自动化测试", RGBColor(83, 107, 72), "REPRO")
    add_card(slide, 8.65, 5.66, 3.95, 1.15, "诚实边界", "演练数据 · 人工审批\n不宣称真实自动指挥", MUTED, "BOUND")
    add_footer(slide, 14)

    # 15 Landing
    slide = prs.slides.add_slide(blank); add_rect(slide, 0, 0, 13.333, 7.5, INK)
    add_title(slide, "14 / LANDING & NEXT", "从可演示 MVP 到可验证的行业产品", dark=True)
    phases = [("01", "演练验证", "园区/学校/医院内部预案推演"), ("02", "合作试点", "授权数据、用户反馈、缺口分析"), ("03", "系统集成", "WMS/ERP、路网、车队、指挥系统"), ("04", "行业模板", "场景 Agent、规则和报告复用")]
    for i, (num, title, body) in enumerate(phases):
        add_card(slide, .72 + i * 3.12, 1.65, 2.82, 2.3, title, body, LIME if i == 0 else RGBColor(120, 157, 141), num)
    add_text(slide, .85, 4.65, 11.8, .55, "高得分关键：不是“能生成”，而是“能验证、能回退、能收敛、能复现”。", 21, WHITE, True)
    add_text(slide, .85, 5.55, 11.8, .45, "急先锋 · Rapid Response OS  /  Agent 编排创新应用", 12, LIME, True)
    add_text(slide, .85, 6.25, 11.8, .35, "演示数据，不替代现场应急指挥。", 9, RGBColor(159, 181, 173))
    add_footer(slide, 15)

    target = DOCS / "急先锋_项目讲解PPT.pptx"
    prs.save(target)
    return target


if __name__ == "__main__":
    html_path = build_document_html()
    ppt_path = build_ppt()
    print(f"HTML: {html_path}")
    print(f"PPTX: {ppt_path}")
