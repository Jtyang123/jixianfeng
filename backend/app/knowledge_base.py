from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from .database import Database, now_iso


class KnowledgeBaseError(ValueError):
    pass


def _extract_docx(payload: bytes) -> str:
    with ZipFile(io.BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n".join(node.text or "" for node in root.findall(".//w:t", namespace))


def extract_text(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json"}:
        return payload.decode("utf-8-sig", errors="replace")
    if suffix == ".docx":
        return _extract_docx(payload)
    if suffix == ".xlsx":
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        rows = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                values = [str(value) for value in row if value not in (None, "")]
                if values:
                    rows.append(" | ".join(values))
        return "\n".join(rows)
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise KnowledgeBaseError("服务器尚未安装 PDF 解析组件，请上传 TXT、Markdown、DOCX 或 XLSX") from exc
        reader = PdfReader(io.BytesIO(payload))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise KnowledgeBaseError("知识文档仅支持 TXT、Markdown、CSV、JSON、DOCX、XLSX 或 PDF")


def _keywords(text: str) -> list[str]:
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    ascii_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", text)
    grams = []
    for block in chinese:
        grams.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
    return [item for item, _ in Counter(grams + ascii_words).most_common(18)]


def chunk_text(text: str, max_chars: int = 700, overlap: int = 80) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text or "").strip()
    if not cleaned:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", cleaned) if item.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start + max_chars])
                start += max_chars - overlap
            continue
        if current and len(current) + len(paragraph) + 1 > max_chars:
            chunks.append(current)
            current = current[-overlap:] + "\n" + paragraph if overlap else paragraph
        else:
            current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return [item.strip() for item in chunks if item.strip()]


SEMANTIC_GROUPS = [
    {"车辆", "运力", "运输", "车队", "趟次", "载重", "车型"},
    {"道路", "路线", "通道", "路段", "封路", "封闭", "通行", "绕行"},
    {"库存", "物资", "存量", "可用量", "缺货", "仓库", "调拨"},
    {"时效", "到达", "截止", "延迟", "超时", "eta", "时限"},
    {"冷链", "冷藏", "冷冻", "温控", "保鲜", "阴凉"},
    {"灾害", "灾情", "洪水", "暴雨", "地震", "火灾", "应急", "救援"},
    {"药品", "医疗", "急救", "卫生", "防疫", "消毒"},
    {"优先", "紧急", "加急", "p0", "p1", "高风险"},
]


def _semantic_terms(text: str) -> list[str]:
    lowered = str(text or "").lower()
    expanded = [lowered]
    for group in SEMANTIC_GROUPS:
        if any(token in lowered for token in group):
            expanded.extend(sorted(group))
    joined = " ".join(expanded)
    terms = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", joined)
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", joined):
        terms.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
        terms.extend(block[index:index + 3] for index in range(max(0, len(block) - 2)))
    return terms


def embed_text(text: str, dimensions: int = 256) -> list[float]:
    vector = [0.0] * dimensions
    counts = Counter(_semantic_terms(text))
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimensions
        sign = 1.0 if (value >> 8) & 1 else -1.0
        vector[index] += sign * (1.0 + math.log1p(count))
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))

def _query_terms(query: str) -> list[str]:
    text = str(query or "").lower()
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", text)
    for block in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
    return list(dict.fromkeys(terms))


class KnowledgeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_document(self, name: str, category: str, source: str, chunks: list[str]) -> dict:
        if not chunks:
            raise KnowledgeBaseError("文档没有可入库的文本内容")
        now = now_iso()
        with self.db.connect() as conn:
            document_id = self.db._insert_id(
                conn,
                "INSERT INTO knowledge_documents (name, category, source, status, chunk_count, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (name, category or "其他", source or "upload", len(chunks), now),
            )
            for index, content in enumerate(chunks, start=1):
                title = content.splitlines()[0][:120] if content.splitlines() else f"{name} 第 {index} 段"
                conn.execute(
                    "INSERT INTO knowledge_chunks (document_id, seq, title, content, keywords, embedding_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (document_id, index, title, content, json.dumps(_keywords(content), ensure_ascii=False), json.dumps(embed_text(content)), now),
                )
        return self.get_document(document_id) or {}

    def list_documents(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM knowledge_documents ORDER BY created_at DESC, id DESC").fetchall()
        return [dict(row) for row in rows]

    def get_document(self, document_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None

    def list_chunks(self, document_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_chunks WHERE document_id = ? ORDER BY seq",
                (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, document_id: int) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM knowledge_documents WHERE id = ?", (document_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (document_id,))
        return True

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        terms = _query_terms(query)
        semantic_query = _semantic_terms(query)
        if not terms and not semantic_query:
            return []
        query_vector = embed_text(query)
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT c.*, d.name AS document_name, d.category
                   FROM knowledge_chunks c
                   JOIN knowledge_documents d ON d.id = c.document_id
                   WHERE d.status = 'active'
                   ORDER BY c.id DESC LIMIT 5000"""
            ).fetchall()
            documents = []
            for raw in rows:
                item = dict(raw)
                try:
                    embedding = json.loads(item.get("embedding_json") or "[]")
                except (TypeError, json.JSONDecodeError):
                    embedding = []
                if len(embedding) != 256:
                    embedding = embed_text(str(item.get("content") or ""))
                    conn.execute(
                        "UPDATE knowledge_chunks SET embedding_json = ? WHERE id = ?",
                        (json.dumps(embedding), item["id"]),
                    )
                item["embedding"] = embedding
                item["tokens"] = Counter(_semantic_terms(str(item.get("content") or "")))
                documents.append(item)

        token_frequency: Counter = Counter()
        for item in documents:
            token_frequency.update(item["tokens"].keys())
        document_count = max(len(documents), 1)
        average_length = sum(sum(item["tokens"].values()) for item in documents) / document_count
        bm25_values = []
        for item in documents:
            length = max(sum(item["tokens"].values()), 1)
            score = 0.0
            for term in terms:
                frequency = item["tokens"].get(term, 0)
                if not frequency:
                    continue
                doc_frequency = max(token_frequency.get(term, 0), 1)
                idf = math.log(1 + (document_count - doc_frequency + 0.5) / (doc_frequency + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / max(average_length, 1))
                score += idf * frequency * 2.5 / denominator
            bm25_values.append(score)
        max_bm25 = max(bm25_values or [0.0])

        ranked = []
        for item, bm25 in zip(documents, bm25_values):
            content = str(item["content"] or "")
            lowered = content.lower()
            exact_matches = sum(1 for term in terms if term in lowered)
            exact_ratio = exact_matches / max(len(terms), 1)
            cosine = cosine_similarity(query_vector, item["embedding"])
            normalized_bm25 = bm25 / max_bm25 if max_bm25 > 0 else 0.0
            semantic_hits = sum(1 for term in set(semantic_query) if term in item["tokens"])
            semantic_ratio = semantic_hits / max(len(set(semantic_query)), 1)
            hybrid_score = cosine * 0.55 + normalized_bm25 * 0.30 + exact_ratio * 0.10 + semantic_ratio * 0.05
            if hybrid_score < 0.035:
                continue
            ranked.append({
                "id": f"KB-{item['document_id']}-{item['seq']}",
                "title": item["title"] or item["document_name"],
                "category": item["category"],
                "source": item["document_name"],
                "excerpt": content[:180],
                "content": content,
                "score": round(hybrid_score, 4),
                "retrieval": {
                    "mode": "domain-hash-vector+bm25+rerank",
                    "vector_similarity": round(cosine, 4),
                    "bm25": round(bm25, 4),
                    "exact_ratio": round(exact_ratio, 4),
                    "semantic_ratio": round(semantic_ratio, 4),
                },
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:max(1, min(top_k, 20))]