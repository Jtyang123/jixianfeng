from pathlib import Path

from app.database import Database
from app.knowledge_base import KnowledgeRepository, embed_text


def test_stale_chunk_embeddings_are_backfilled_in_one_batch(tmp_path: Path):
    db = Database(tmp_path / "knowledge-batch.db", database_url="")
    db.initialize()
    repository = KnowledgeRepository(db)
    repository.insert_document(
        "route-rules", "transport", "test", [f"flood route rule {index}" for index in range(10)]
    )
    with db.connect() as conn:
        conn.execute("UPDATE knowledge_chunks SET embedding_provider='stale', embedding_dim=0")

    meta = {"provider": "remote", "model": "test-model", "dimensions": 256, "mode": "remote"}
    repository.embedding.remote = True
    repository.embedding.provider = "remote"
    repository.embedding.model = "test-model"
    repository.embedding.dimensions = 256
    repository.embedding.embed = lambda query: (embed_text(query, dimensions=256), dict(meta))
    calls = []

    def fake_embed_many(texts):
        calls.append(list(texts))
        return [(embed_text(text, dimensions=256), dict(meta)) for text in texts]

    repository.embedding.embed_many = fake_embed_many
    results = repository.search("flood route", top_k=4)
    assert results
    assert len(calls) == 1
    assert len(calls[0]) == 10
    repository.search("flood route", top_k=4)
    assert len(calls) == 1