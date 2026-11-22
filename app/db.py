from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    version       INTEGER NOT NULL DEFAULT 0,
    base_version  INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS row_def (
    row_key TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    pos     INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS col_def (
    col_key TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    pos     INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS base_cell (
    row_key     TEXT NOT NULL REFERENCES row_def(row_key) ON DELETE CASCADE,
    col_key     TEXT NOT NULL REFERENCES col_def(col_key) ON DELETE CASCADE,
    amount_yuan INTEGER NOT NULL CHECK (amount_yuan >= 0),
    PRIMARY KEY (row_key, col_key)
);

CREATE TABLE IF NOT EXISTS public_cell (
    row_key   TEXT NOT NULL REFERENCES row_def(row_key) ON DELETE CASCADE,
    col_key   TEXT NOT NULL REFERENCES col_def(col_key) ON DELETE CASCADE,
    value_wan INTEGER NOT NULL CHECK (value_wan >= 0),
    PRIMARY KEY (row_key, col_key)
);

CREATE TABLE IF NOT EXISTS public_row (
    row_key      TEXT PRIMARY KEY REFERENCES row_def(row_key) ON DELETE CASCADE,
    subtotal_wan INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS public_col (
    col_key      TEXT PRIMARY KEY REFERENCES col_def(col_key) ON DELETE CASCADE,
    subtotal_wan INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS public_total (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    total_wan INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lock_record (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    row_key           TEXT NOT NULL UNIQUE REFERENCES row_def(row_key) ON DELETE CASCADE,
    row_label         TEXT NOT NULL,
    subtotal_wan      INTEGER NOT NULL,
    cells_json        TEXT NOT NULL,
    statement_version INTEGER NOT NULL,
    locked_at         TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def init_db(db_path: str) -> None:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta (id, version, base_version, updated_at) VALUES (1, 0, 0, ?)",
            (now_iso(),),
        )
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE: 串行化写事务，版本检查与写入之间不会被其他写者插入。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
