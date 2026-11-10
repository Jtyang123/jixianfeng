import threading
from pathlib import Path

from app.database import Database
from app import knowledge_base as knowledge_base_module
from app.knowledge_base import EmbeddingService, KnowledgeRepository, embed_text


def test_stale_chunk_embeddings_are_backfilled_in_one_batch(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "knowledge-batch.db", database_url="")
    db.initialize()
    repository = KnowledgeRepository(db)
    repository.insert_document(
        "route-rules", "transport", "test", [f"flood route rule {index}" for index in range(10)]
    )
    with db.connect() as conn:
        conn.execute("UPDATE knowledge_chunks SET embedding_provider='stale', embedding_dim=0")

    meta = {"provider": "remote", "model": "test-model", "dimensions": 256, "mode": "remote"}
    monkeypatch.setattr(repository.embedding, "remote", True)
    monkeypatch.setattr(repository.embedding, "provider", "remote")
    monkeypatch.setattr(repository.embedding, "model", "test-model")
    monkeypatch.setattr(repository.embedding, "dimensions", 256)
    monkeypatch.setattr(repository.embedding, "embed", lambda query: (embed_text(query, dimensions=256), dict(meta)))
    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake_embed_many(texts):
        calls.append(list(texts))
        started.set()
        release.wait(2.0)
        return [(embed_text(text, dimensions=256), dict(meta)) for text in texts]

    monkeypatch.setattr(repository.embedding, "embed_many", fake_embed_many)
    results = repository.search("flood route", top_k=4)
    assert results
    assert all(item["retrieval"]["space_mismatch"] is True for item in results)
    assert all(item["retrieval"]["vector_similarity"] == 0.0 for item in results)
    assert started.wait(1.0) is True
    with db.connect() as conn:
        providers = [row["embedding_provider"] for row in conn.execute("SELECT embedding_provider FROM knowledge_chunks").fetchall()]
    assert set(providers) == {"stale"}
    release.set()
    assert repository.wait_for_embedding_backfill(2.0) is True
    assert len(calls) == 1
    assert len(calls[0]) == 10
    repository.search("flood route", top_k=4)
    assert len(calls) == 1

def test_offline_embedding_uses_deterministic_hash_fallback():
    service = EmbeddingService()
    service.local_model = None
    service.remote = False
    vector, meta = service.embed("flood route backup")
    assert len(vector) == service.dimensions
    assert meta["provider"] == "hash"
    assert service.status["semantic_retrieval"] is False

def test_knowledge_repository_reuses_shared_embedding_service_and_cache(tmp_path: Path):
    db = Database(tmp_path / "shared-embedding.db", database_url="")
    db.initialize()
    first = KnowledgeRepository(db)
    second = KnowledgeRepository(db)
    assert first.embedding is second.embedding
    assert first._corpus_cache is second._corpus_cache


def test_local_model_schedules_backfill(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "local-backfill.db", database_url="")
    db.initialize()
    repository = KnowledgeRepository(db)
    submitted = []

    class FakeExecutor:
        def submit(self, fn, ids):
            submitted.append((fn, ids))

    monkeypatch.setattr(knowledge_base_module, "_EMBEDDING_BACKFILL_EXECUTOR", FakeExecutor())
    monkeypatch.setattr(repository.embedding, "remote", False)
    monkeypatch.setattr(repository.embedding, "local_model", object())
    with knowledge_base_module._EMBEDDING_BACKFILL_LOCK:
        knowledge_base_module._EMBEDDING_BACKFILL_PENDING.clear()
    repository._schedule_embedding_backfill([11, 12])
    assert submitted and submitted[0][1] == [11, 12]
    with knowledge_base_module._EMBEDDING_BACKFILL_LOCK:
        knowledge_base_module._EMBEDDING_BACKFILL_PENDING.clear()
