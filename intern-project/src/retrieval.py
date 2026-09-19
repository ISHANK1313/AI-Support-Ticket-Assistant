"""Whole-policy cosine retrieval. Provider calls never hold SQLite connections."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from . import database
from .config import Settings
from .schemas import TicketInput

CHUNKER_VERSION = "whole-doc-v1"
MAX_DOCUMENTS = 32
MAX_DOCUMENT_BYTES = 16_000
MAX_DIMENSION = 16_384


class IndexUnavailable(RuntimeError):
    """Missing, stale, corrupt index or unavailable embedding provider."""


def _corpus(path: Path) -> list[tuple[str, str]]:
    paths = sorted(Path(path).glob("*.md"))
    if not paths or len(paths) > MAX_DOCUMENTS:
        raise IndexUnavailable("Policy corpus unavailable")
    docs = []
    for file in paths:
        if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_DOCUMENT_BYTES:
            raise IndexUnavailable("Policy corpus unavailable")
        content = file.read_text(encoding="utf-8").strip()
        if not content:
            raise IndexUnavailable("Policy corpus unavailable")
        docs.append((file.name, content))
    return docs


def _digest(docs: list[tuple[str, str]]) -> str:
    encoded = json.dumps(docs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize(vector, dimension=None) -> np.ndarray:
    # float64 norm avoids float32 overflow; persistent format is little-endian float32.
    arr = np.asarray(vector, dtype=np.float64)
    if arr.ndim != 1 or not 0 < arr.size <= MAX_DIMENSION:
        raise IndexUnavailable("Invalid embedding vector")
    if dimension is not None and arr.size != dimension:
        raise IndexUnavailable("Incompatible embedding dimension")
    if not np.isfinite(arr).all():
        raise IndexUnavailable("Invalid embedding vector")
    scale = np.max(np.abs(arr))
    if scale == 0:
        raise IndexUnavailable("Invalid embedding vector")
    arr = arr / scale
    return (arr / np.linalg.norm(arr)).astype("<f4")


class GeminiEmbedder:
    """Lazy SDK boundary; one attempt, 30s timeout, bounded batch size.

    gemini-embedding-001 uses retrieval task types. Embedding 2 instead requires
    task instructions in text; vector spaces remain separated by stored model id.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _embed(self, texts: list[str], query: bool):
        from .provider import run_provider
        if not self.settings.gemini_api_key:
            raise IndexUnavailable("Embedding provider unavailable")
        result = run_provider("embed_query" if query else "embed_documents", self.settings, texts)
        if not isinstance(result, list) or len(result) != len(texts):
            raise IndexUnavailable("Embedding provider unavailable")
        return result

    def embed_documents(self, texts: list[str]):
        return self._embed(texts, query=False)

    def embed_query(self, text: str):
        return self._embed([text], query=True)[0]


class RetrievalService:
    """Inject embed_documents(list[str]) and embed_query(str) for offline tests.

    Snapshot reads are transactional, then closed before provider work. No cache:
    every request validates the full tiny index against the current corpus.
    """

    def __init__(self, settings: Settings, embedder=None):
        self.settings = settings
        self.embedder = embedder if embedder is not None else GeminiEmbedder(settings)

    def _snapshot(self, docs):
        if not self.settings.database_path.is_file():
            raise IndexUnavailable("Policy index unavailable")
        with database.get_conn(self.settings.database_path) as conn:
            conn.execute("BEGIN")
            meta = conn.execute("SELECT * FROM policy_index WHERE id=1").fetchone()
            rows = conn.execute(
                "SELECT doc_name, chunk_index, content, embedding FROM policy_chunks "
                "WHERE index_id=1 ORDER BY doc_name, chunk_index"
            ).fetchall()
        if (meta is None or meta["embedding_model"] != self.settings.gemini_embedding_model
                or meta["corpus_hash"] != _digest(docs) or meta["chunker_version"] != CHUNKER_VERSION):
            raise IndexUnavailable("Policy index unavailable")
        dimension = meta["embedding_dimension"]
        if not isinstance(dimension, int) or not 0 < dimension <= MAX_DIMENSION:
            raise IndexUnavailable("Policy index unavailable")
        expected = [(name, 0, text) for name, text in docs]
        if [(r["doc_name"], r["chunk_index"], r["content"]) for r in rows] != expected:
            raise IndexUnavailable("Policy index unavailable")
        vectors = []
        for row in rows:
            blob = row["embedding"]
            if not isinstance(blob, bytes) or len(blob) != dimension * 4:
                raise IndexUnavailable("Policy index unavailable")
            vector = np.frombuffer(blob, dtype="<f4")
            if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
                raise IndexUnavailable("Policy index unavailable")
            vectors.append(vector)
        return dimension, rows, np.stack(vectors)

    def ingest(self) -> dict:
        """Reuse valid index, otherwise embed fully before atomic replacement."""
        try:
            docs = _corpus(self.settings.knowledge_base_path)
            digest = _digest(docs)
            try:
                dimension, rows, _ = self._snapshot(docs)
                return {"chunks": len(rows), "dimension": dimension, "corpus_hash": digest, "reused": True}
            except Exception:
                pass  # An absent/stale/corrupt index can be explicitly rebuilt.
            raw = self.embedder.embed_documents([text for _, text in docs])
            if len(raw) != len(docs):
                raise IndexUnavailable("Embedding count mismatch")
            vectors = [_normalize(v) for v in raw]
            dimension = len(vectors[0])
            if any(len(v) != dimension for v in vectors):
                raise IndexUnavailable("Embedding dimension mismatch")
            if _corpus(self.settings.knowledge_base_path) != docs:
                raise IndexUnavailable("Policy corpus changed during ingestion")
            # Schema bootstrap uses the project's single DDL source, after embedding.
            self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
            database.init_db(self.settings.database_path)
            with database.get_conn(self.settings.database_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM policy_chunks")
                conn.execute("DELETE FROM policy_index")
                conn.execute(
                    "INSERT INTO policy_index(id,embedding_model,embedding_dimension,corpus_hash,chunker_version) "
                    "VALUES(1,?,?,?,?)", (self.settings.gemini_embedding_model, dimension, digest, CHUNKER_VERSION),
                )
                conn.executemany(
                    "INSERT INTO policy_chunks(index_id,doc_name,chunk_index,content,embedding) VALUES(1,?,0,?,?)",
                    [(name, text, vector.tobytes()) for (name, text), vector in zip(docs, vectors)],
                )
            return {"chunks": len(docs), "dimension": dimension, "corpus_hash": digest, "reused": False}
        except Exception:
            raise IndexUnavailable("Policy index could not be built") from None

    def retrieve(self, ticket: TicketInput) -> list[dict]:
        try:
            docs = _corpus(self.settings.knowledge_base_path)
            dimension, rows, vectors = self._snapshot(docs)
            query = json.dumps(ticket.model_dump(mode="json"), ensure_ascii=False)
            vector = _normalize(self.embedder.embed_query(query), dimension)
            if _corpus(self.settings.knowledge_base_path) != docs:
                raise IndexUnavailable("Policy corpus changed during retrieval")
            scores = np.clip(vectors @ vector, -1.0, 1.0)
            indices = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["doc_name"]))
            k = min(len(rows), max(3, int(self.settings.retrieval_top_k)))
            return [{"doc_name": rows[i]["doc_name"], "chunk_index": rows[i]["chunk_index"],
                     "content": rows[i]["content"], "score": float(scores[i])} for i in indices[:k]]
        except Exception:
            raise IndexUnavailable("Policy index or embedding provider unavailable") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the local Gemini policy index")
    parser.add_argument("command", choices=["ingest"])
    parser.parse_args(argv)
    try:
        result = RetrievalService(Settings.load()).ingest()
    except (RuntimeError, ValueError, OSError):
        print(json.dumps({"error": "INDEX_UNAVAILABLE", "message": "Check configuration and policy index availability"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
