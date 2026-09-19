"""Offline index tests: real SQLite/vector math, deterministic provider boundary."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from src.config import Settings
from src.schemas import TicketInput
from src.retrieval import IndexUnavailable, RetrievalService


@pytest.fixture
def settings(tmp_path):
    kb = tmp_path / "policies"
    kb.mkdir()
    for name in ("a", "b", "c", "d"):
        (kb / f"{name}.md").write_text(f"# {name}\n\nEntire coherent policy {name}.\n", encoding="utf-8")
    return Settings(gemini_api_key="", gemini_model="gemini-2.5-flash",
                    gemini_embedding_model="gemini-embedding-001", jwt_secret="offline-test-secret",
                    jwt_expire_minutes=60, database_path=tmp_path / "test.db",
                    knowledge_base_path=kb, retrieval_top_k=3)


class Embedder:
    def __init__(self):
        self.document_calls = 0
        self.query_text = None
        self.fail = False

    def embed_documents(self, texts):
        self.document_calls += 1
        if self.fail:
            raise TimeoutError("provider private detail")
        return [[i + 1, 1, 0] for i, _ in enumerate(texts)]

    def embed_query(self, text):
        self.query_text = text
        return [1, 0, 0]


def snapshot(settings):
    with sqlite3.connect(settings.database_path) as conn:
        return (conn.execute("SELECT * FROM policy_index").fetchall(),
                conn.execute("SELECT * FROM policy_chunks ORDER BY id").fetchall())


def test_ingest_whole_documents_normalized_and_idempotent(settings):
    embedder = Embedder()
    service = RetrievalService(settings, embedder)
    first = service.ingest()
    before = snapshot(settings)
    assert first["chunks"] == 4 and first["reused"] is False
    assert service.ingest()["reused"] is True
    assert embedder.document_calls == 1
    assert snapshot(settings) == before
    for row in before[1]:
        assert row[3] == 0
        assert row[4] == (settings.knowledge_base_path / row[2]).read_text().strip()
        vector = np.frombuffer(row[5], dtype="<f4")
        assert np.linalg.norm(vector) == pytest.approx(1.0)


def test_cosine_top_k_at_least_three_and_canonical_facts(settings):
    embedder = Embedder()
    service = RetrievalService(replace(settings, retrieval_top_k=1), embedder)
    service.ingest()
    ticket = TicketInput(message="Where is it?", days_since_dispatch=0, order_value_inr="2000.00")
    results = service.retrieve(ticket)
    assert [r["doc_name"] for r in results] == ["d.md", "c.md", "b.md"]
    assert all(set(r) == {"doc_name", "chunk_index", "content", "score"} for r in results)
    assert results[0]["score"] == pytest.approx(4 / np.sqrt(17))
    assert json.loads(embedder.query_text) == ticket.model_dump(mode="json")


def test_failed_rebuild_preserves_previous_index(settings):
    embedder = Embedder()
    service = RetrievalService(settings, embedder)
    service.ingest()
    before = snapshot(settings)
    (settings.knowledge_base_path / "a.md").write_text("# Changed policy")
    embedder.fail = True
    with pytest.raises(IndexUnavailable):
        service.ingest()
    assert snapshot(settings) == before


def test_rebuild_removes_deleted_chunks(settings):
    service = RetrievalService(settings, Embedder())
    first_hash = service.ingest()["corpus_hash"]
    (settings.knowledge_base_path / "a.md").unlink()
    second = service.ingest()
    assert second["corpus_hash"] != first_hash and second["chunks"] == 3
    assert {r[2] for r in snapshot(settings)[1]} == {"b.md", "c.md", "d.md"}


@pytest.mark.parametrize("corruption", ["stale_file", "model", "dimension", "chunker", "nan", "zero", "length", "content", "missing_chunk"])
def test_unusable_index_rejected_before_query_network(settings, corruption):
    embedder = Embedder()
    service = RetrievalService(settings, embedder)
    service.ingest()
    with sqlite3.connect(settings.database_path) as conn:
        if corruption == "stale_file":
            (settings.knowledge_base_path / "a.md").write_text("changed")
        elif corruption in {"model", "dimension", "chunker"}:
            column, value = {"model": ("embedding_model", "different"), "dimension": ("embedding_dimension", 2),
                             "chunker": ("chunker_version", "old")}[corruption]
            conn.execute(f"UPDATE policy_index SET {column}=?", (value,))
        elif corruption in {"nan", "zero", "length"}:
            blob = {"nan": np.array([np.nan, 0, 0], dtype="<f4").tobytes(),
                    "zero": bytes(12), "length": bytes(3)}[corruption]
            conn.execute("UPDATE policy_chunks SET embedding=? WHERE id=(SELECT min(id) FROM policy_chunks)", (blob,))
        elif corruption == "content":
            conn.execute("UPDATE policy_chunks SET content='changed'")
        else:
            conn.execute("DELETE FROM policy_chunks WHERE doc_name='a.md'")
    with pytest.raises(IndexUnavailable):
        service.retrieve(TicketInput(message="question"))
    assert embedder.query_text is None


@pytest.mark.parametrize("vector", [[0, 0, 0], [float("nan"), 1, 0], [1, 2], [float("inf"), 0, 0]])
def test_invalid_query_vectors(settings, vector):
    embedder = Embedder()
    service = RetrievalService(settings, embedder)
    service.ingest()
    embedder.embed_query = lambda text: vector
    with pytest.raises(IndexUnavailable):
        service.retrieve(TicketInput(message="question"))


def test_missing_index_and_empty_corpus_are_operational_errors(settings):
    service = RetrievalService(settings, Embedder())
    with pytest.raises(IndexUnavailable):
        service.retrieve(TicketInput(message="question"))
    for path in settings.knowledge_base_path.glob("*.md"):
        path.unlink()
    with pytest.raises(IndexUnavailable):
        service.ingest()


def test_no_database_connection_open_during_provider_calls(settings, monkeypatch):
    from src import database
    original = database.connect
    active = []

    class TrackedConnection:
        def __init__(self, conn):
            self.conn = conn
            active.append(self)

        def __getattr__(self, key):
            return getattr(self.conn, key)

        def close(self):
            active.remove(self)
            self.conn.close()

    monkeypatch.setattr(database, "connect", lambda path: TrackedConnection(original(path)))
    embedder = Embedder()
    docs, query = embedder.embed_documents, embedder.embed_query

    def checked_docs(texts):
        assert not active
        return docs(texts)

    def checked_query(text):
        assert not active
        return query(text)

    embedder.embed_documents, embedder.embed_query = checked_docs, checked_query
    service = RetrievalService(settings, embedder)
    service.ingest()
    service.retrieve(TicketInput(message="question"))
    assert not active
