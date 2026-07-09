# src/fearnation_mcp/server.py
"""MCP server with 4 tools: search_news, get_post, list_recent, discover.

Tools auto-refresh RSS if last_rss_fetch > 60 min old (spec §6.1).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from mcp.server import MCPServer

from fearnation_mcp.db import DB_PATH, get_connection, get_meta
from fearnation_mcp.utils import get_logger, validate_iso_date, validate_slug

log = get_logger(__name__)

# Test override for "today" — None in production.
_TODAY_OVERRIDE: date | None = None

mcp = MCPServer("FearNation")


def _get_conn() -> sqlite3.Connection:
    """Get a DB connection (real path). Tests monkeypatch this."""
    return get_connection(DB_PATH)


def _maybe_refresh_rss(conn: sqlite3.Connection) -> None:
    """Refresh RSS in background if last_rss_fetch is stale (>60 min)."""
    last = get_meta(conn, "last_rss_fetch")
    if last is None:
        # Never fetched — startup owns the bootstrap crawl.
        return
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return
    age = datetime.now(UTC) - last_dt
    if age > timedelta(minutes=60):
        try:
            from fearnation_mcp.crawler import refresh_rss

            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                refresh_rss(client, conn)
        except Exception as exc:  # noqa: BLE001 — log + continue serving
            log.warning("rss background refresh failed", extra={"error": str(exc)})


def search_news(
    query: str,
    section: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search FearNation news items by full-text query.

    Cross-script: Simplified Chinese queries also match Traditional content
    (e.g., "华为" matches "華為") via OpenCC normalization.
    """
    from fearnation_mcp.search import search_items

    conn = _get_conn()
    _maybe_refresh_rss(conn)
    hits = search_items(
        conn,
        query,
        section=section,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return [
        {
            "slug": h.slug,
            "section": h.section,
            "headline": h.headline,
            "body": h.body_text,
            "pub_date": h.pub_date,
            "seq": h.seq,
        }
        for h in hits
    ]


def get_post(slug_or_date: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Get a full post by slug or ISO date.

    For a date with multiple posts, returns a list of post summaries.
    For slug (or single-post date), returns the full post with items
    and financial_data.
    """
    conn = _get_conn()
    _maybe_refresh_rss(conn)

    is_date = False
    try:
        validate_iso_date(slug_or_date)
        is_date = True
    except ValueError:
        validate_slug(slug_or_date)

    if is_date:
        rows = conn.execute(
            "SELECT slug, title, pub_date, post_type FROM posts "
            "WHERE pub_date = ? ORDER BY slug",
            (slug_or_date,),
        ).fetchall()
        if not rows:
            raise KeyError(f"No post found for date {slug_or_date}")
        if len(rows) == 1:
            return _fetch_full_post(conn, rows[0]["slug"])
        return [
            {
                "slug": r["slug"],
                "title": r["title"],
                "pub_date": r["pub_date"],
                "post_type": r["post_type"],
            }
            for r in rows
        ]
    return _fetch_full_post(conn, slug_or_date)


def _fetch_full_post(conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
    post = conn.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
    if post is None:
        raise KeyError(f"No post found with slug {slug!r}")
    items = conn.execute(
        "SELECT section, headline, body_text, pub_date, seq "
        "FROM items WHERE post_slug = ? ORDER BY seq",
        (slug,),
    ).fetchall()
    fin = conn.execute(
        "SELECT field, value FROM financial_data WHERE post_slug = ? ORDER BY field", (slug,)
    ).fetchall()
    return {
        "slug": post["slug"],
        "title": post["title"],
        "pub_date": post["pub_date"],
        "post_type": post["post_type"],
        "items": [
            {
                "section": i["section"],
                "headline": i["headline"],
                "body": i["body_text"],
                "pub_date": i["pub_date"],
                "seq": i["seq"],
            }
            for i in items
        ],
        "financial_data": [{"field": f["field"], "value": f["value"]} for f in fin],
    }


def list_recent(days: int = 7) -> list[dict[str, Any]]:
    """List recent posts within the last N days.

    Each result has slug, title, pub_date, post_type, item_count.
    """
    if days < 1 or days > 365:
        raise ValueError("days must be in [1, 365]")
    conn = _get_conn()
    _maybe_refresh_rss(conn)
    today = _TODAY_OVERRIDE or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT p.slug, p.title, p.pub_date, p.post_type, "
        "(SELECT COUNT(*) FROM items WHERE post_slug = p.slug) AS item_count "
        "FROM posts p WHERE p.pub_date >= ? ORDER BY p.pub_date DESC",
        (cutoff,),
    ).fetchall()
    return [
        {
            "slug": r["slug"],
            "title": r["title"],
            "pub_date": r["pub_date"],
            "post_type": r["post_type"],
            "item_count": r["item_count"],
        }
        for r in rows
    ]


def discover(
    query: str | None = None,
    post_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Browse the post catalogue. Returns post-level summaries (not items).

    Filter by title substring (query), post_type, or date range.
    At least one filter recommended; with none, returns the most recent 50.
    """
    if date_from:
        validate_iso_date(date_from)
    if date_to:
        validate_iso_date(date_to)

    conn = _get_conn()
    sql = "SELECT slug, title, pub_date, post_type FROM posts WHERE 1=1"
    params: list[Any] = []
    if query:
        sql += " AND title LIKE ?"
        params.append(f"%{query}%")
    if post_type:
        sql += " AND post_type = ?"
        params.append(post_type)
    if date_from:
        sql += " AND pub_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND pub_date <= ?"
        params.append(date_to)
    sql += " ORDER BY pub_date DESC LIMIT 50"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "slug": r["slug"],
            "title": r["title"],
            "pub_date": r["pub_date"],
            "post_type": r["post_type"],
        }
        for r in rows
    ]


# --- MCP tool registration ---


@mcp.tool()
def t_search_news(
    query: str,
    section: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search FearNation news items by full-text query.

    Cross-script: Simplified Chinese queries match Traditional content too
    (e.g., "华为" matches "華為"). Returns items with slug, section, headline,
    body, pub_date, seq.

    Args:
        query: Free-text search string.
        section: Optional section filter (中国新闻 / 印太新闻 / 科技新闻 / 经济新闻).
        date_from: Optional ISO date YYYY-MM-DD (inclusive).
        date_to: Optional ISO date YYYY-MM-DD (inclusive).
        limit: Max results (1-200, default 20).
    """
    return search_news(query, section=section, date_from=date_from, date_to=date_to, limit=limit)


@mcp.tool()
def t_get_post(slug_or_date: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Get a full post by slug (e.g. "shijie-kucha-2024-01-15") or ISO date.

    If multiple posts exist for a date, returns a list of post summaries.
    For a single post, returns items + financial_data.
    """
    return get_post(slug_or_date)


@mcp.tool()
def t_list_recent(days: int = 7) -> list[dict[str, Any]]:
    """List posts from the last N days. Use to orient before searching.

    Each result has slug, title, pub_date, post_type, item_count.
    """
    return list_recent(days=days)


@mcp.tool()
def t_discover(
    query: str | None = None,
    post_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Browse the post catalogue. Returns post-level summaries (not items).

    Filter by title substring (query), post_type ("世界苦茶" or "台海危機ALERT"),
    or date range (ISO YYYY-MM-DD). At least one filter recommended.
    """
    return discover(query=query, post_type=post_type, date_from=date_from, date_to=date_to)


def run() -> None:
    """Run the MCP server over stdio transport (entry point)."""
    mcp.run(transport="stdio")
