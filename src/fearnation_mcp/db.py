# src/fearnation_mcp/db.py
"""SQLite schema, connection management, and idempotent upserts.

Storage lives under $XDG_CACHE_HOME/fearnation_mcp/fearnation.db
(or ~/.cache/fearnation_mcp/fearnation.db if XDG_CACHE_HOME unset).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from fearnation_mcp.utils import get_logger

log = get_logger(__name__)


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "fearnation_mcp"
    return Path(os.path.expanduser("~/.cache")) / "fearnation_mcp"


_DB_DIR = _cache_dir()
DB_PATH = _DB_DIR / "fearnation.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    pub_date TEXT,
    post_type TEXT,
    raw_html TEXT,
    parsed_at TEXT,
    lastmod TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    post_slug TEXT NOT NULL REFERENCES posts(slug) ON DELETE CASCADE,
    section TEXT,
    headline TEXT,
    headline_norm TEXT,
    body_text TEXT,
    body_norm TEXT,
    seq INTEGER,
    pub_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_pub_date ON items(pub_date);
CREATE INDEX IF NOT EXISTS idx_items_post_slug ON items(post_slug);

CREATE TABLE IF NOT EXISTS financial_data (
    id INTEGER PRIMARY KEY,
    post_slug TEXT NOT NULL REFERENCES posts(slug) ON DELETE CASCADE,
    field TEXT,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_financial_post_slug ON financial_data(post_slug);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    headline_norm,
    body_norm,
    tokenize='unicode61 remove_diacritics 2'
);
"""


@dataclass(frozen=True)
class PostRow:
    slug: str
    title: str
    pub_date: str | None
    post_type: str | None
    raw_html: str | None
    lastmod: str | None = None
    parsed_at: str | None = None
    last_seen: str | None = None


@dataclass(frozen=True)
class ItemRow:
    section: str | None
    headline: str | None
    headline_norm: str | None
    body_text: str | None
    body_norm: str | None
    seq: int
    pub_date: str | None


@dataclass(frozen=True)
class FinancialDataRow:
    field: str
    value: str


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist. Idempotent."""
    conn.executescript(_SCHEMA)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with WAL, FK enforcement, schema initialized."""
    if db_path is None:
        db_path = DB_PATH
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def upsert_post(conn: sqlite3.Connection, post: PostRow) -> None:
    """Idempotent upsert of a post row."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    post = replace(post, last_seen=now, parsed_at=post.parsed_at or now)
    conn.execute(
        """
        INSERT INTO posts (
            slug, title, pub_date, post_type, raw_html, parsed_at, lastmod, last_seen
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title=COALESCE(NULLIF(excluded.title, ''), posts.title),
            pub_date=COALESCE(excluded.pub_date, posts.pub_date),
            post_type=COALESCE(excluded.post_type, posts.post_type),
            raw_html=COALESCE(NULLIF(excluded.raw_html, ''), posts.raw_html),
            parsed_at=excluded.parsed_at,
            lastmod=COALESCE(excluded.lastmod, posts.lastmod),
            last_seen=excluded.last_seen
        """,
        (
            post.slug,
            post.title,
            post.pub_date,
            post.post_type,
            post.raw_html,
            post.parsed_at,
            post.lastmod,
            post.last_seen,
        ),
    )


def upsert_items(
    conn: sqlite3.Connection,
    post_slug: str,
    items: Iterable[ItemRow],
    pub_date: str | None,
) -> None:
    """Replace all items for a post_slug. Double-writes FTS5 to keep in sync.

    Plain FTS5 table — managed rowid pairs with items.id.
    """
    existing_ids = [
        r[0] for r in conn.execute("SELECT id FROM items WHERE post_slug=?", (post_slug,))
    ]
    if existing_ids:
        placeholders = ",".join("?" * len(existing_ids))
        conn.execute(f"DELETE FROM items_fts WHERE rowid IN ({placeholders})", existing_ids)
    conn.execute("DELETE FROM items WHERE post_slug=?", (post_slug,))

    for item in items:
        cur = conn.execute(
            """
            INSERT INTO items
                (post_slug, section, headline, headline_norm, body_text, body_norm, seq, pub_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_slug,
                item.section,
                item.headline,
                item.headline_norm,
                item.body_text,
                item.body_norm,
                item.seq,
                pub_date,
            ),
        )
        new_id = cur.lastrowid
        if new_id is not None:
            conn.execute(
                "INSERT INTO items_fts (rowid, headline_norm, body_norm) VALUES (?, ?, ?)",
                (new_id, item.headline_norm, item.body_norm),
            )


def upsert_financial_data(
    conn: sqlite3.Connection,
    post_slug: str,
    rows: Iterable[FinancialDataRow],
) -> None:
    """Replace all financial_data rows for a post_slug."""
    conn.execute("DELETE FROM financial_data WHERE post_slug=?", (post_slug,))
    for row in rows:
        conn.execute(
            "INSERT INTO financial_data (post_slug, field, value) VALUES (?, ?, ?)",
            (post_slug, row.field, row.value),
        )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None
