from __future__ import annotations

import argparse
import json
import sys

from app.config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
)
from app.database import Database
from app.knowledge_base import KnowledgeRepository


REMOTE_PROVIDERS = {"remote", "openai", "openai-compatible"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Embedding preflight")
    parser.add_argument("--probe", default="道路封闭 车辆载重", help="retrieval probe text")
    args = parser.parse_args()

    if EMBEDDING_PROVIDER in REMOTE_PROVIDERS:
        missing = []
        if not EMBEDDING_BASE_URL:
            missing.append("EMBEDDING_BASE_URL")
        if not EMBEDDING_MODEL:
            missing.append("EMBEDDING_MODEL")
        if not EMBEDDING_API_KEY:
            missing.append("EMBEDDING_API_KEY")
        if missing:
            print(json.dumps({
                "status": "configuration_error",
                "provider": EMBEDDING_PROVIDER,
                "missing": missing,
                "message": "remote embedding selected but required variables are missing",
            }, ensure_ascii=False, indent=2))
            return 1

    db = Database()
    db.initialize()
    repository = KnowledgeRepository(db)
    repository.ensure_seed_documents()
    status = repository.embedding_stats()
    results = repository.search(args.probe, top_k=3)
    top3 = [
        {
            "rank": index,
            "score": item.get("score"),
            "cos": (item.get("retrieval") or {}).get("vector_similarity"),
            "bm25": (item.get("retrieval") or {}).get("bm25"),
            "title": item.get("title"),
            "content_30": str(item.get("content") or "")[:30],
        }
        for index, item in enumerate(results, start=1)
    ]
    print(json.dumps({
        "probe": args.probe,
        "provider": status.get("provider"),
        "model": status.get("model"),
        "dimensions": status.get("dimensions"),
        "semantic_retrieval": status.get("semantic_retrieval"),
        "total_chunks": status.get("total_chunks"),
        "stale_chunks": status.get("stale_chunks"),
        "mode": status.get("mode"),
        "top3": top3,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())