-- Support-Ticket Decision Assistant schema (SQLite).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL CHECK(length(trim(message)) > 0),
    facts_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS ix_tickets_owner_time
    ON tickets(user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
    action TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    sources TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS policy_index (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL CHECK(embedding_dimension > 0),
    corpus_hash TEXT NOT NULL,
    chunker_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS policy_chunks (
    id INTEGER PRIMARY KEY,
    index_id INTEGER NOT NULL REFERENCES policy_index(id),
    doc_name TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    content TEXT NOT NULL,
    embedding BLOB NOT NULL,
    UNIQUE(index_id, doc_name, chunk_index)
);
