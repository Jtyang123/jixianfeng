from __future__ import annotations

import io
import json
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
                    "INSERT INTO knowledge_chunks (document_id, seq, title, content, keywords, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (document_id, index, title, content, json.dumps(_keywords(content), ensure_ascii=False), now),
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
        if not terms:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT c.*, d.name AS document_name, d.category
                   FROM knowledge_chunks c
                   JOIN knowledge_documents d ON d.id = c.document_id
                   WHERE d.status = 'active'
                   ORDER BY c.id DESC LIMIT 5000"""
            ).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            content = str(item["content"] or "")
            lowered = content.lower()
            matches = sum(1 for term in terms if term in lowered)
            if not matches:
                continue
            score = round(matches / max(len(terms), 1), 4)
            ranked.append({
                "id": f"KB-{item['document_id']}-{item['seq']}",
                "title": item["title"] or item["document_name"],
                "category": item["category"],
                "source": item["document_name"],
                "excerpt": content[:180],
                "content": content,
                "score": score,
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:max(1, min(top_k, 20))]
