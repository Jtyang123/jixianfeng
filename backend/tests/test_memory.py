from datetime import datetime, timedelta
from pathlib import Path

import app.memory as memory_module
from app.database import Database, now_iso
from app.memory import MemoryRepository


class FakeEmbeddingService:
    local_model = object()
    remote = False
    provider = "fake-semantic"
    model = "fake-2d"
    dimensions = 2

    @staticmethod
    def _meta():
        return {"provider": "fake-semantic", "model": "fake-2d", "dimensions": 2}

    def embed(self, text: str):
        if text == "暴雨备用通道路线":
            return [0.1, 0.9], self._meta()
        if "台风天气绕行" in text:
            return [0.0, 1.0], self._meta()
        if "暴雨备用通道" in text or "强降雨绕行" in text:
            return [1.0, 0.0], self._meta()
        return [0.5, 0.5], self._meta()

    def embed_many(self, texts: list[str]):
        return [self.embed(text) for text in texts]


class UnavailableEmbeddingService:
    local_model = None
    remote = False
    provider = "hash"
    model = "domain-hash-v1"
    dimensions = 2

    def embed(self, text: str):
        raise RuntimeError("embedding unavailable")

    def embed_many(self, texts: list[str]):
        raise RuntimeError("embedding unavailable")


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
def test_memory_embedding_is_persisted_with_provider_and_dimensions(tmp_path: Path):
    db = Database(tmp_path / "memory-vector.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = FakeEmbeddingService()
    row = repository.remember("matching_agent", "route_memory", {"route": "暴雨备用通道"}, 0.9, "RUN-V")
    assert row["embedding"] == [1.0, 0.0]
    assert row["embedding_provider"] == "fake-semantic"
    assert row["embedding_model"] == "fake-2d"
    assert row["embedding_dim"] == 2


def test_memory_hybrid_ranking_combines_vector_and_keyword(tmp_path: Path):
    db = Database(tmp_path / "memory-hybrid.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = FakeEmbeddingService()
    repository.remember("matching_agent", "keyword_route", {"route": "暴雨备用通道"}, 0.8, "RUN-A")
    repository.remember("matching_agent", "semantic_route", {"route": "台风天气绕行策略"}, 0.8, "RUN-B")
    ranked = repository.summary(["matching_agent"], query="暴雨备用通道路线", per_agent_limit=5)
    keys = list(ranked["matching_agent"].keys())
    assert keys.index("semantic_route") < keys.index("keyword_route")
    semantic = ranked["matching_agent"]["semantic_route"]
    keyword = ranked["matching_agent"]["keyword_route"]
    assert semantic["vector_similarity"] > keyword["vector_similarity"]
    assert semantic["retrieval_mode"] == "hybrid-vector-keyword"
    assert semantic["retrieval_score"] == round(semantic["vector_similarity"] * 0.6 + semantic["keyword_relevance"] * 0.4, 4)


def test_memory_keyword_fallback_when_embedding_unavailable(tmp_path: Path):
    db = Database(tmp_path / "memory-fallback.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = UnavailableEmbeddingService()
    repository.remember("matching_agent", "route_memory", {"route": "暴雨备用通道"}, 0.9, "RUN-C")
    ranked = repository.summary(["matching_agent"], query="暴雨备用通道路线", per_agent_limit=1)
    payload = ranked["matching_agent"]["route_memory"]
    assert payload["retrieval_mode"] == "keyword-fallback"
    assert payload["relevance"] > 0


def test_memory_old_rows_are_backfilled_after_keyword_fallback(tmp_path: Path):
    db = Database(tmp_path / "memory-backfill.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = FakeEmbeddingService()
    now = now_iso()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO agent_memory
               (agent_id, memory_key, value_json, confidence, source_run_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("matching_agent", "legacy_memory", '{"route":"暴雨备用通道"}', 0.9, "RUN-OLD", now, now),
        )
    ranked = repository.summary(["matching_agent"], query="暴雨备用通道路线", per_agent_limit=1)
    assert ranked["matching_agent"]["legacy_memory"]["retrieval_mode"] == "keyword-fallback"
    assert repository.wait_for_embedding_backfill(timeout=3)
    row = next(item for item in repository.list_memories("matching_agent") if item["memory_key"] == "legacy_memory")
    assert row["embedding"]
    assert row["embedding_provider"] == "fake-semantic"


def test_memory_decay_penalizes_old_low_importance_entries(tmp_path: Path):
    db = Database(tmp_path / "memory-decay.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = FakeEmbeddingService()
    repository.remember("matching_agent", "new_fact", {"route": "强降雨绕行"}, 0.9, "RUN-NEW")
    repository.remember("matching_agent", "old_fact", {"route": "强降雨绕行"}, 0.9, "RUN-OLD")
    old_time = (datetime.now().astimezone() - timedelta(days=120)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE agent_memory SET updated_at=?, last_accessed_at=NULL WHERE agent_id=? AND memory_key=?",
            (old_time, "matching_agent", "old_fact"),
        )
    ranked = repository.summary(["matching_agent"], query="强降雨绕行", per_agent_limit=2)
    assert list(ranked["matching_agent"].keys())[:2] == ["new_fact", "old_fact"]
    old_row = next(item for item in repository.list_memories("matching_agent") if item["memory_key"] == "old_fact")
    assert old_row["recency_score"] < 0.1
    assert old_row["importance"] < ranked["matching_agent"]["new_fact"]["importance"]


def test_memory_cleanup_removes_expired_and_audits_without_silent_delete(tmp_path: Path):
    db = Database(tmp_path / "memory-cleanup.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = UnavailableEmbeddingService()
    repository.remember("matching_agent", "low_fact", {"route": "旧低置信度"}, 0.2, "RUN-LOW")
    repository.remember("matching_agent", "high_fact", {"route": "仍需保留"}, 0.9, "RUN-HIGH")
    repository.record_repair_experience(
        ["ROAD-R-A"], "rainstorm-ii", "route_failover", "旧策略", "旧策略",
        False, "RUN-REPAIR", effect=1.0, superseded=True,
    )
    old_time = (datetime.now().astimezone() - timedelta(days=120)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE agent_memory SET updated_at=?", (old_time,))
    result = repository.cleanup_expired()
    assert result["deleted"] == 2
    remaining = {item["memory_key"] for item in repository.list_memories("matching_agent")}
    assert remaining == {"high_fact"}
    audit = repository.list_cleanup_audit()
    assert {item["memory_key"] for item in audit} == {"low_fact", next(key for key in ["repair_experience:rainstorm-ii:ROAD-R-A:route_failover"])}
    assert {item["reason"] for item in audit} == {"low-confidence-retention-expired", "superseded-repair-experience"}


def test_memory_context_budget_truncates_long_values_and_adds_summary(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "memory-budget.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = UnavailableEmbeddingService()
    repository.remember("matching_agent", "long_fact", {"summary": "长产物", "details": "x" * 500}, 0.99, "RUN-1")
    repository.remember("matching_agent", "second_fact", {"route": "第二条"}, 0.8, "RUN-2")
    repository.remember("matching_agent", "third_fact", {"route": "第三条"}, 0.7, "RUN-3")
    monkeypatch.setattr(memory_module, "MEMORY_CONTEXT_BUDGET_ITEMS", 2)
    monkeypatch.setattr(memory_module, "MEMORY_CONTEXT_BUDGET_CHARS", 1000)
    monkeypatch.setattr(memory_module, "MEMORY_ENTRY_SUMMARY_CHARS", 40)
    ranked = repository.summary(["matching_agent"], query="长产物", per_agent_limit=10)
    memory = ranked["matching_agent"]
    assert memory["long_fact"]["truncated"] is True
    assert memory["long_fact"]["value"]["summary"].startswith("规则摘要：")
    assert memory["__budget_summary__"]["value"]["omitted"] == 1
    assert len([key for key in memory if not key.startswith("__")]) == 2


def test_memory_experience_and_fact_classes_are_separated(tmp_path: Path):
    db = Database(tmp_path / "memory-classes.db", database_url="")
    db.initialize()
    repository = MemoryRepository(db)
    repository.embedding = UnavailableEmbeddingService()
    repository.remember("matching_agent", "route_memory", {"route": "暴雨备用通道"}, 0.95, "RUN-FACT")
    for index in range(3):
        repository.record_repair_experience(
            ["ROAD-R-A"], "rainstorm-ii", "route_failover", "路线故障转移", "排除封闭路线",
            True, f"RUN-EXP-{index}", effect=12.0, superseded=False,
        )
    ranked = repository.summary(["matching_agent"], query="暴雨备用通道 ROAD-R-A", per_agent_limit=10)
    keys = [key for key in ranked["matching_agent"] if not key.startswith("__")]
    assert keys[0].startswith("repair_experience:")
    assert ranked["matching_agent"][keys[0]]["memory_class"] == "repair_experience"
    assert ranked["matching_agent"]["route_memory"]["memory_class"] == "fact"
    experiences = repository.find_repair_experiences(["ROAD-R-A"], "rainstorm-ii", min_success_rate=0.0)
    assert experiences
    assert all(item["memory_class"] == "repair_experience" for item in experiences)
    assert all(item["memory_key"] != "route_memory" for item in experiences)
