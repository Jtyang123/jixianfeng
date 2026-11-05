from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class KnowledgeDoc:
    id: str
    title: str
    category: str
    content: str
    keywords: list[str]
    source: str
    updated_at: str


KNOWLEDGE_DOCS = [
    KnowledgeDoc(
        "KB-DEMAND-001", "安置点生活保障需求测算规则", "需求规则",
        "按安置人口、灾害等级和保障时长测算饮用水、食品、药品和照明需求。饮用水按每人每日1.5升起步，II级灾害需提高医疗与照明保障优先级。",
        ["需求", "安置点", "饮用水", "医疗", "II级", "优先级"], "应急演练规则库 / 模拟", "2026-11-01",
    ),
    KnowledgeDoc(
        "KB-ROUTE-002", "汛期主通道封闭处置规则", "道路规则",
        "暴雨洪涝期间，主通道积水或桥涵风险未确认时不得列入可执行方案。应优先启用备用通道，并在核验报告中保留封闭路线证据。",
        ["道路", "封闭", "主通道", "备用通道", "暴雨", "积水"], "运输管控规则库 / 模拟", "2026-11-01",
    ),
    KnowledgeDoc(
        "KB-INV-003", "多仓库存分配与锁定规则", "库存规则",
        "应急调度分配量不得超过库存快照中的可用量扣除锁定量的结果。跨仓调拨应优先满足P0物资，并记录仓库、批次和库存快照版本。",
        ["库存", "多仓", "锁定", "P0", "批次", "快照"], "物资管理规则库 / 模拟", "2026-11-01",
    ),
    KnowledgeDoc(
        "KB-VEH-004", "应急车辆载重与趟次规则", "运力规则",
        "车辆趟次由货物总重量和可用最大载重计算。必要趟次超过仓库可用车辆数量时，应作为运力风险提示并给出增援或跨仓方案。",
        ["车辆", "载重", "趟次", "运力", "应急"], "物流方案库 / 模拟", "2026-11-01",
    ),
    KnowledgeDoc(
        "KB-VERIFY-005", "Plan-Do-Check-Act 核验退回规范", "验证规则",
        "方案生成后必须由独立核验角色检查。发现硬阻断时退回生成角色，反馈仅包含问题代码、证据和修正要求。连续两轮不通过时转入人工研判。",
        ["核验", "退回", "反馈", "自我循环", "人工研判", "PDCA"], "Agent 工程规范 / 项目自建", "2026-11-01",
    ),
    KnowledgeDoc(
        "KB-DELIVERY-006", "调度交付包最低字段要求", "交付规则",
        "交付包必须包括推荐方案、备选方案、物资分配明细、路线、预计时效、成本、风险提示、执行清单、数据依据和免责声明。",
        ["交付", "备选", "风险", "执行清单", "数据依据"], "应急演练交付规范 / 模拟", "2026-11-01",
    ),
]


def _grams(text: str) -> list[str]:
    text = re.sub(r"\s+", "", text.lower())
    latin = re.findall(r"[a-z0-9]+", text)
    chinese = re.sub(r"[^\u4e00-\u9fff]", "", text)
    grams = list(latin) + list(chinese) + [chinese[i:i+2] for i in range(max(0, len(chinese)-1))]
    return grams


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    query_terms = Counter(_grams(query))
    scored = []
    for doc in KNOWLEDGE_DOCS:
        haystack = f"{doc.title} {doc.content} {' '.join(doc.keywords)} {doc.category}"
        doc_terms = Counter(_grams(haystack))
        overlap = sum(min(count, doc_terms.get(term, 0)) for term, count in query_terms.items())
        keyword_hits = sum(2.5 for keyword in doc.keywords if keyword in query)
        length_penalty = 1 + math.log1p(len(doc.content) / 80)
        score = round((overlap + keyword_hits) / length_penalty, 3)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            **asdict(doc),
            "score": score,
            "excerpt": doc.content[:88] + ("..." if len(doc.content) > 88 else ""),
        }
        for score, doc in scored[:top_k]
    ]


def catalog() -> list[dict]:
    return [asdict(doc) for doc in KNOWLEDGE_DOCS]
