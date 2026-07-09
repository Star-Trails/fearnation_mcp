# src/fearnation_mcp/server.py
"""MCP server with 4 tools: search_news, get_post, list_recent, discover.

Tool wrappers auto-refresh RSS if last_rss_fetch > 60 min old (spec §6.1),
and trigger a weekly sitemap sweep if last_sitemap_sweep > 7 days (spec §3).

On first run (empty DB), an MCP lifespan handler backgrounds a bootstrap
``crawl_all()`` so startup is not blocked (spec §3 «首次启动时一次性全量爬取»,
«不阻塞启动»). Subsequent runs fall through to the normal RSS cooldown.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server import MCPServer

from fearnation_mcp.db import DB_PATH, get_connection, get_meta, set_meta
from fearnation_mcp.utils import (
    get_logger,
    make_http_client,
    validate_iso_date,
    validate_slug,
)

log = get_logger(__name__)

# Test override for "today" — None in production.
_TODAY_OVERRIDE: date | None = None


def _get_conn() -> sqlite3.Connection:
    """Get a DB connection (real path). Tests monkeypatch this."""
    return get_connection(DB_PATH)


def _maybe_refresh_rss(conn: sqlite3.Connection) -> None:
    """Refresh RSS in background if last_rss_fetch is stale (> 60 min).

    If ``last_rss_fetch`` is None (never fetched — e.g. a brand-new install
    whose bootstrap crawl has just finished), attempt ``refresh_rss`` once
    so the meta key gets populated; subsequent calls then observe the
    cooldown. Errors are swallowed (RSS failures must not break user
    queries — spec §6.1).
    """
    last = get_meta(conn, "last_rss_fetch")
    if last is not None:
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return
        age = datetime.now(UTC) - last_dt
        if age <= timedelta(minutes=60):
            return  # fresh enough
    # Either never fetched OR stale → refresh now.
    try:
        from fearnation_mcp.crawler import refresh_rss

        with make_http_client(timeout=15.0, follow_redirects=True) as client:
            refresh_rss(client, conn)
    except Exception as exc:  # noqa: BLE001 — log + continue serving
        log.warning("rss background refresh failed", extra={"error": str(exc)})


def _maybe_weekly_sweep(conn: sqlite3.Connection) -> None:
    """Background a full sitemap sweep if last_sitemap_sweep > 7 days (spec §3).

    A daemon thread runs ``crawl_all()`` so the calling tool is not blocked.
    On first run (``last_sitemap_sweep`` is None), this defers to the
    lifespan bootstrap crawl to avoid spawning two concurrent full crawls.

    The conn passed in is only used for the meta read on the calling thread;
    the background thread opens its own connection via :func:`_get_conn`
    (sqlite3 connections are not thread-safe by default, so a fresh
    connection per thread is required).
    """
    last = get_meta(conn, "last_sitemap_sweep")
    if last is None:
        # First-run bootstrap is owned by the lifespan handler.
        return
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return
    if datetime.now(UTC) - last_dt <= timedelta(days=7):
        return  # swept recently

    def _bg() -> None:
        try:
            from fearnation_mcp.crawler import crawl_all

            bg_conn = _get_conn()
            with make_http_client(timeout=30.0, follow_redirects=True) as client:
                crawl_all(client, bg_conn)
                set_meta(
                    bg_conn,
                    "last_sitemap_sweep",
                    datetime.now(UTC).isoformat(timespec="seconds"),
                )
            bg_conn.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("weekly sitemap sweep failed", extra={"error": str(exc)})

    threading.Thread(target=_bg, daemon=True).start()


def _bootstrap_metadata(conn: sqlite3.Connection) -> None:
    """First-run check: background a bootstrap ``crawl_all()`` if needed.

    Invoked from the MCP lifespan handler — does not block startup. Subsequent
    runs fall through to the normal RSS cooldown refresh. Also pre-parses any
    orphaned posts whose ``parsed_at IS NULL`` using stored ``raw_html``
    (spec §3 «启动时 re-parse parsed_at IS NULL»); this covers posts that
    failed to parse on a previous run and are no longer in the sitemap.

    The conn passed in is only used for the meta read on the calling thread;
    the background thread opens its own connection via :func:`_get_conn`
    (sqlite3 connections are not thread-safe by default).
    """
    needs_bootstrap = (
        get_meta(conn, "full_crawl_done") is None and get_meta(conn, "last_sitemap_sweep") is None
    )

    def _bg() -> None:
        bg_conn = _get_conn()
        # Cheap self-heal first: re-parse stored-but-unparsed posts (no network).
        try:
            from fearnation_mcp.crawler import reparse_pending

            reparse_pending(bg_conn)
        except Exception as exc:  # noqa: BLE001
            log.warning("startup reparse failed", extra={"error": str(exc)})

        if needs_bootstrap:
            try:
                from fearnation_mcp.crawler import crawl_all

                with make_http_client(timeout=30.0, follow_redirects=True) as client:
                    crawl_all(client, bg_conn)
                    set_meta(
                        bg_conn,
                        "last_sitemap_sweep",
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("bootstrap crawl failed", extra={"error": str(exc)})
        else:
            # Subsequent runs: normal RSS cooldown refresh (no-op if fresh).
            _maybe_refresh_rss(bg_conn)
        bg_conn.close()

    if needs_bootstrap:
        threading.Thread(target=_bg, daemon=True).start()
    else:
        # Already bootstrapped — just refresh RSS (cheap, possibly no-op).
        _maybe_refresh_rss(conn)


@contextlib.asynccontextmanager
async def _app_lifespan(server: MCPServer[Any]) -> AsyncGenerator[dict[str, Any]]:
    """Background-bootstrap crawl on first run; do not block startup (spec §3)."""
    try:
        _bootstrap_metadata(_get_conn())
    except Exception as exc:  # noqa: BLE001
        log.warning("lifespan init failed", extra={"error": str(exc)})
    yield {}


mcp = MCPServer("FearNation", lifespan=_app_lifespan)


# --- MCP tool registration (names per spec §6 + README §Tools) ---


@mcp.tool()
def search_news(
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
    from fearnation_mcp.search import search_items

    conn = _get_conn()
    _maybe_refresh_rss(conn)
    _maybe_weekly_sweep(conn)
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


@mcp.tool()
def get_post(slug_or_date: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Get a full post by slug (e.g. "shijie-kucha-2024-01-15") or ISO date.

    If multiple posts exist for a date, returns a list of post summaries.
    For a single post, returns items + financial_data.
    """
    conn = _get_conn()
    _maybe_refresh_rss(conn)
    _maybe_weekly_sweep(conn)

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


@mcp.tool()
def list_recent(days: int = 7) -> list[dict[str, Any]]:
    """List posts from the last N days. Use to orient before searching.

    Each result has slug, title, pub_date, post_type, item_count.
    """
    if days < 1 or days > 365:
        raise ValueError("days must be in [1, 365]")
    conn = _get_conn()
    _maybe_refresh_rss(conn)
    _maybe_weekly_sweep(conn)
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


@mcp.tool()
def discover(
    query: str | None = None,
    post_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Browse the post catalogue. Returns post-level summaries (not items).

    Filter by title substring (query), post_type ("世界苦茶" or "台海危機ALERT"),
    or date range (ISO YYYY-MM-DD). At least one filter recommended.
    """
    if date_from:
        validate_iso_date(date_from)
    if date_to:
        validate_iso_date(date_to)

    conn = _get_conn()
    _maybe_refresh_rss(conn)
    _maybe_weekly_sweep(conn)
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


def run() -> None:
    """Run the MCP server over stdio transport (entry point)."""
    mcp.run(transport="stdio")
