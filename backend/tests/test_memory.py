from datetime import datetime, timedelta
from pathlib import Path

from app.database import Database
from app.memory import MemoryRepository


def test_memory_summary_uses_importance_recency_and_query_relevance(tmp_path: Path):
    db = Database(tmp_path / "memory.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.remember("matching_agent", "route_memory", {"route": "暴雨备用通道"}, 0.9, "RUN-1")
    repository.remember("matching_agent", "other_memory", {"route": "普通库存提示"}, 0.5, "RUN-1")
    ranked = repository.summary(["matching_agent"], query="暴雨备用通道路线", per_agent_limit=1)
    assert "route_memory" in ranked["matching_agent"]
    payload = ranked["matching_agent"]["route_memory"]
    assert payload["importance"] > 0
    assert payload["relevance"] > 0
    assert payload["retrieval_score"] > 0

    old_time = (datetime.now().astimezone() - timedelta(days=90)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE agent_memory SET updated_at=? WHERE agent_id=? AND memory_key=?",
            (old_time, "matching_agent", "route_memory"),
        )
    old_row = next(
        item for item in repository.list_memories("matching_agent")
        if item["memory_key"] == "route_memory"
    )
    assert old_row["age_days"] >= 89
    assert old_row["recency_score"] < 0.1