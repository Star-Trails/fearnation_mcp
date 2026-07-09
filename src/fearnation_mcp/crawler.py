# src/fearnation_mcp/crawler.py
"""Sitemap recursion, RSS parse, full-crawl driver with retry.

Strategy (spec §3):
  - First run: fetch sitemap (recursively if sitemapindex), then fetch each
    post HTML at 1 req/sec, parse, upsert to DB.
  - Incremental: refresh_rss checks last_rss_fetch >60 min, fetches RSS,
    upserts new posts.
  - Self-healing: on startup, re-parse posts where parsed_at IS NULL or
    parsed_at < lastmod.
"""

from __future__ import annotations

import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser  # type: ignore[reportMissingTypeStubs]
import httpx

from fearnation_mcp.db import (
    FinancialDataRow,
    ItemRow,
    PostRow,
    set_meta,
    upsert_financial_data,
    upsert_items,
    upsert_post,
)
from fearnation_mcp.parser import ParsedPost, parse_post
from fearnation_mcp.search import normalize_text
from fearnation_mcp.utils import build_post_url, get_logger, validate_slug

log = get_logger(__name__)

_BASE_URL = "https://fearnation.club/"
_SITEMAP_URL = _BASE_URL + "sitemap.xml"
_RSS_URL = _BASE_URL + "rss/"


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: str | None = None
    is_sitemap: bool = False


@dataclass(frozen=True)
class RSSItem:
    slug: str
    title: str
    pub_date: str | None
    content_html: str
    link: str


@dataclass
class CrawlReport:
    posts_fetched: int = 0
    posts_failed: int = 0
    items_extracted: int = 0
    financial_rows: int = 0
    duration_sec: float = 0.0


def fetch_url(client: httpx.Client, url: str, timeout: float = 15.0) -> str:
    """Fetch URL text, raise on non-2xx."""
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _localname(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_sitemap(xml_text: str) -> list[SitemapEntry]:
    """Parse sitemap XML (sitemapindex or urlset). Returns SitemapEntry list.

    - sitemapindex entries: is_sitemap=True (caller recurses).
    - urlset entries: is_sitemap=False (actual post URLs).
    """
    if not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("sitemap parse failed", extra={"error": str(exc)})
        return []

    tag = _localname(root.tag)
    entries: list[SitemapEntry] = []

    if tag == "sitemapindex":
        for sm in root:
            if _localname(sm.tag) != "sitemap":
                continue
            loc = lastmod = None
            for child in sm:
                ln = _localname(child.tag)
                if ln == "loc":
                    loc = child.text
                elif ln == "lastmod":
                    lastmod = child.text
            if loc:
                entries.append(SitemapEntry(loc=loc, lastmod=lastmod, is_sitemap=True))
    elif tag == "urlset":
        for url in root:
            if _localname(url.tag) != "url":
                continue
            loc = lastmod = None
            for child in url:
                ln = _localname(child.tag)
                if ln == "loc":
                    loc = child.text
                elif ln == "lastmod":
                    lastmod = child.text
            if loc:
                entries.append(SitemapEntry(loc=loc, lastmod=lastmod, is_sitemap=False))
    return entries


def _slug_from_link(link: str) -> str:
    """Extract slug from a fearnation URL."""
    path = link.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    validate_slug(slug)
    return slug


def parse_rss(rss_xml: str) -> list[RSSItem]:
    """Parse RSS 2.0 feed — return list of RSSItem."""
    if not rss_xml.strip():
        return []
    fp: Any = feedparser
    parsed: Any = fp.parse(rss_xml)
    items: list[RSSItem] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "")
        content_html: str = ""
        if hasattr(entry, "content") and entry.content:
            content_html = entry.content[0].value
        elif hasattr(entry, "content_encoded"):
            content_html = entry.content_encoded
        pub_date: str | None = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                dt = datetime(*entry.published_parsed[:6], tzinfo=UTC)
                pub_date = dt.strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass
        try:
            slug = _slug_from_link(link)
        except ValueError:
            log.warning("rss entry has invalid link, skipping", extra={"link": link})
            continue
        items.append(
            RSSItem(
                slug=slug,
                title=title,
                pub_date=pub_date,
                content_html=content_html,
                link=link,
            )
        )
    return items


def upsert_parsed_post(
    conn: sqlite3.Connection,
    slug: str,
    raw_html: str,
    parsed: ParsedPost,
    lastmod: str | None = None,
) -> int:
    """Upsert a parsed post + items + financial_data in a single txn.

    Binding contract (Task 3 reviewer): ``upsert_post`` / ``upsert_items`` /
    ``upsert_financial_data`` are caller-cooperative — they issue DML on the
    shared connection without opening a transaction context. To honor spec
    §3.3 ("单个 txn 内双写") and guarantee per-post atomicity on crash mid-crawl,
    this wrapper opens a single ``with conn:`` context that auto-commits on
    clean exit (and rolls back on exception), so partial posts never appear in
    queries mid-crawl. One transaction per post.

    The ``_norm`` column values (``headline_norm``, ``body_norm``) are the
    output of :func:`fearnation_mcp.search.normalize_text` — the same OpenCC
    t2s + CJK-char-split form used by the runtime search layer. Per Task 6
    resolution, callers MUST NOT compute ``_norm`` any other way; doing so
    would break query/index consistency.

    Returns the number of items inserted.
    """
    item_rows = [
        ItemRow(
            section=item.section,
            headline=item.headline,
            headline_norm=normalize_text(item.headline or ""),
            body_text=item.body_text,
            body_norm=normalize_text(item.body_text or ""),
            seq=item.seq,
            pub_date=parsed.pub_date,
        )
        for item in parsed.items
    ]
    fin_rows = [FinancialDataRow(field=r.field, value=r.value) for r in parsed.financial_data]

    with conn:  # txn — auto-commits on success, rolls back on exception
        upsert_post(
            conn,
            PostRow(
                slug=slug,
                title=parsed.title,
                pub_date=parsed.pub_date,
                post_type=parsed.post_type,
                raw_html=raw_html,
                lastmod=lastmod,
            ),
        )
        upsert_items(conn, slug, item_rows, pub_date=parsed.pub_date)
        upsert_financial_data(conn, slug, fin_rows)
    return len(item_rows)


def crawl_post(
    client: httpx.Client,
    conn: sqlite3.Connection,
    slug: str,
    lastmod: str | None = None,
) -> int:
    """Fetch + parse + upsert a single post by slug. Returns item count."""
    url = build_post_url(slug)
    raw_html = fetch_url(client, url)
    parsed = parse_post(slug, raw_html)
    return upsert_parsed_post(conn, slug, raw_html, parsed, lastmod=lastmod)


def crawl_all(
    client: httpx.Client,
    conn: sqlite3.Connection,
    rate_limit_sec: float = 1.0,
    max_retries: int = 3,
) -> CrawlReport:
    """Full crawl: fetch sitemap(s) recursively, then fetch every post.

    Idempotent — safe to call multiple times. Posts already in DB with
    unchanged lastmod are skipped.
    """
    start = time.time()
    report = CrawlReport()

    try:
        sitemap_xml = fetch_url(client, _SITEMAP_URL)
    except httpx.HTTPError as exc:
        log.error(
            "root sitemap fetch failed",
            extra={"url": _SITEMAP_URL, "error": str(exc)},
        )
        report.duration_sec = time.time() - start
        return report

    # Recursively collect all post URLs
    pending_sitemaps = [_SITEMAP_URL]
    visited_sitemaps: set[str] = set()
    post_entries: list[SitemapEntry] = []

    while pending_sitemaps:
        sm_url = pending_sitemaps.pop()
        if sm_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sm_url)
        try:
            xml_text = sitemap_xml if sm_url == _SITEMAP_URL else fetch_url(client, sm_url)
        except httpx.HTTPError as exc:
            log.warning(
                "child sitemap fetch failed",
                extra={"url": sm_url, "error": str(exc)},
            )
            continue
        entries = parse_sitemap(xml_text)
        for entry in entries:
            if entry.is_sitemap:
                pending_sitemaps.append(entry.loc)
            else:
                post_entries.append(entry)

    log.info(
        "sitemap recursion complete",
        extra={
            "total_sitemaps": len(visited_sitemaps),
            "total_post_urls": len(post_entries),
        },
    )

    for entry in post_entries:
        try:
            slug = _slug_from_link(entry.loc)
        except ValueError:
            log.warning("invalid sitemap URL skipped", extra={"loc": entry.loc})
            report.posts_failed += 1
            continue

        # Skip if already indexed and lastmod unchanged
        existing = conn.execute(
            "SELECT parsed_at, lastmod FROM posts WHERE slug=?", (slug,)
        ).fetchone()
        if (
            existing
            and existing["parsed_at"]
            and existing["lastmod"]
            and entry.lastmod
            and existing["lastmod"] >= entry.lastmod
        ):
            continue

        attempt = 0
        succeeded = False
        last_err: str | None = None
        while attempt < max_retries and not succeeded:
            attempt += 1
            try:
                item_count = crawl_post(client, conn, slug, lastmod=entry.lastmod)
                report.posts_fetched += 1
                report.items_extracted += item_count
                succeeded = True
            except (httpx.HTTPError, OSError) as exc:
                last_err = str(exc)
                log.warning(
                    "post fetch retry",
                    extra={"slug": slug, "attempt": attempt, "error": last_err},
                )
                time.sleep(rate_limit_sec * attempt)

        if not succeeded:
            report.posts_failed += 1
            log.error(
                "post fetch failed after retries",
                extra={"slug": slug, "error": last_err, "attempts": attempt},
            )

        if rate_limit_sec > 0:
            time.sleep(rate_limit_sec)

    set_meta(
        conn,
        "full_crawl_done",
        datetime.now(UTC).isoformat(timespec="seconds"),
    )
    report.duration_sec = time.time() - start
    log.info(
        "full crawl complete",
        extra={
            "posts_fetched": report.posts_fetched,
            "posts_failed": report.posts_failed,
            "items_extracted": report.items_extracted,
            "duration_sec": report.duration_sec,
        },
    )
    return report


def _wrap_rss_html(content_html: str, title: str, pub_date: str) -> str:
    """Wrap RSS content:encoded into a full HTML doc for parser."""
    pub_iso = f"{pub_date}T08:00:00.000Z" if pub_date else ""
    return f"""<!DOCTYPE html>
<html><head>
<title>{title}</title>
<meta property="article:published_time" content="{pub_iso}">
</head><body>
<main class="post-content">
{content_html}
</main></body></html>
"""


def refresh_rss(client: httpx.Client, conn: sqlite3.Connection) -> int:
    """Fetch RSS, upsert new posts. Returns count of new/updated posts."""
    try:
        rss_xml = fetch_url(client, _RSS_URL)
    except httpx.HTTPError as exc:
        log.error("rss fetch failed", extra={"url": _RSS_URL, "error": str(exc)})
        return 0

    items = parse_rss(rss_xml)
    new_count = 0
    for item in items:
        wrapped_html = _wrap_rss_html(item.content_html, item.title, item.pub_date or "")
        parsed = parse_post(item.slug, wrapped_html)
        upsert_parsed_post(conn, item.slug, item.content_html, parsed)
        new_count += 1

    set_meta(
        conn,
        "last_rss_fetch",
        datetime.now(UTC).isoformat(timespec="seconds"),
    )
    log.info("rss refresh complete", extra={"new_posts": new_count})
    return new_count
