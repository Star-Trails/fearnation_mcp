# tests/test_smoke.py
"""Live network smoke tests. Skipped by default.

Run manually before release with:
    uv run pytest -m network tests/test_smoke.py
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from fearnation_mcp.crawler import (
    _slug_from_link,  # type: ignore[reportPrivateUsage]
    crawl_all,
    fetch_url,
    parse_rss,
    parse_sitemap,
)
from fearnation_mcp.db import init_schema
from fearnation_mcp.parser import parse_post
from fearnation_mcp.robots import fetch_robots_rules


@pytest.mark.network
def test_robots_txt_reachable() -> None:
    with httpx.Client(timeout=15.0) as client:
        rules = fetch_robots_rules(client)
        assert rules.is_allowed("/rss/")


@pytest.mark.network
def test_sitemap_reachable() -> None:
    with httpx.Client(timeout=15.0) as client:
        sitemap_xml = fetch_url(client, "https://fearnation.club/sitemap.xml")
        assert "<urlset" in sitemap_xml or "<sitemapindex" in sitemap_xml


@pytest.mark.network
def test_rss_reachable() -> None:
    with httpx.Client(timeout=15.0) as client:
        rss_xml = fetch_url(client, "https://fearnation.club/rss/")
        assert "<rss" in rss_xml or "<feed" in rss_xml
        items = parse_rss(rss_xml)
        assert len(items) > 0


@pytest.mark.network
def test_single_post_fetch_and_parse() -> None:
    """Fetch one post and verify parser handles real Ghost HTML."""
    with httpx.Client(timeout=15.0) as client:
        sitemap_xml = fetch_url(client, "https://fearnation.club/sitemap.xml")
        entries = parse_sitemap(sitemap_xml)
        if entries and entries[0].is_sitemap:
            child_xml = fetch_url(client, entries[0].loc)
            entries = parse_sitemap(child_xml)
        post_entries = [e for e in entries if not e.is_sitemap]
        assert post_entries
        first = post_entries[0]
        slug = _slug_from_link(first.loc)
        html = fetch_url(client, first.loc)
        parsed = parse_post(slug, html)
        assert parsed.title
        assert parsed.items or parsed.financial_data


@pytest.mark.network
def test_full_crawl_smoke() -> None:
    """End-to-end crawl smoke test. May take ~5 min."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        report = crawl_all(client, conn, rate_limit_sec=1.0)
    assert report.posts_fetched > 0
