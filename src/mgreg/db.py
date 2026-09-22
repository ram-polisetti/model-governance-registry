"""SQLite storage layer: connection handling, schema, and the clock hook.

The clock is injectable (``Registry(..., now=...)``) so tests and
reproducible runs can fix timestamps; production defaults to UTC now.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    owner       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_cards (
    id          TEXT PRIMARY KEY,
    model_id    TEXT NOT NULL REFERENCES models(id),
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    UNIQUE (model_id, version)
);

CREATE TABLE IF NOT EXISTS risk_assessments (
    id          TEXT PRIMARY KEY,
    model_id    TEXT NOT NULL REFERENCES models(id),
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    overall_risk TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    UNIQUE (model_id, version)
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    model_id    TEXT NOT NULL REFERENCES models(id),
    version     INTEGER NOT NULL,
    approver    TEXT NOT NULL,
    decision    TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    UNIQUE (model_id, version)
);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    model_id    TEXT NOT NULL REFERENCES models(id),
    kind        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_hash   TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    model_id    TEXT,
    payload     TEXT NOT NULL,
    actor       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_model ON model_cards(model_id);
CREATE INDEX IF NOT EXISTS idx_risks_model ON risk_assessments(model_id);
CREATE INDEX IF NOT EXISTS idx_approvals_model ON approvals(model_id);
CREATE INDEX IF NOT EXISTS idx_evidence_model ON evidence(model_id);
CREATE INDEX IF NOT EXISTS idx_events_model ON audit_events(model_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


NowFn = Callable[[], str]
