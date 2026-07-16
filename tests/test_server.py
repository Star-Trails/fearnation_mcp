# tests/test_server.py
"""Tests for server.py — call tool functions directly without MCP transport."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from fearnation_mcp.db import ItemRow, PostRow, init_schema, set_meta, upsert_items, upsert_post
from fearnation_mcp.search import normalize_text
from fearnation_mcp.server import (
    _parse_meta_timestamp,
    discover,
    get_post,
    list_recent,
    search_news,
)


def _seed_full(conn: sqlite3.Connection, today: date = date(2024, 1, 16)) -> None:
    """Seed 3 posts dated Jan 14-15 2024."""
    seed = [
        ("shijie-kucha-2024-01-15", "世界苦茶 2024-01-15", "2024-01-15", "世界苦茶"),
        ("shijie-kucha-2024-01-14", "世界苦茶 2024-01-14", "2024-01-14", "世界苦茶"),
        ("taiwan-alert-2024-01-14", "台海危機ALERT 2024-01-14", "2024-01-14", "台海危機ALERT"),
    ]
    for slug, title, pub_date, pt in seed:
        upsert_post(
            conn, PostRow(slug=slug, title=title, pub_date=pub_date, post_type=pt, raw_html="x")
        )
        items = [
            ItemRow(
                section="中国新闻",
                headline=f"{title} 华为新闻",
                headline_norm=normalize_text(f"{title} 华为新闻"),
                body_text="正文",
                body_norm="正文",
                seq=0,
                pub_date=pub_date,
            ),
        ]
        upsert_items(conn, slug, items, pub_date=pub_date)
    # Pretend RSS is fresh so server doesn't attempt network refresh
    set_meta(conn, "last_rss_fetch", datetime.now(UTC).isoformat(timespec="seconds"))


class TestMetaTimestamp:
    def test_malformed_value_is_treated_as_missing(self) -> None:
        assert _parse_meta_timestamp("not-a-date") is None

    def test_legacy_naive_value_is_treated_as_utc(self) -> None:
        assert _parse_meta_timestamp("2024-01-15T12:00:00") == datetime(2024, 1, 15, 12, tzinfo=UTC)


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    _seed_full(c)
    import fearnation_mcp.server as srv

    monkeypatch.setattr(srv, "_get_conn", lambda: c)
    monkeypatch.setattr(srv, "_TODAY_OVERRIDE", date(2024, 1, 16))
    return c


class TestSearchNews:
    def test_basic_search(self, conn: sqlite3.Connection) -> None:
        hits = search_news("华为")
        assert len(hits) >= 3
        for hit in hits:
            assert "slug" in hit
            assert "headline" in hit
            assert "pub_date" in hit

    def test_section_filter(self, conn: sqlite3.Connection) -> None:
        hits = search_news("华为", section="中国新闻")
        assert len(hits) >= 1
        assert all(h["section"] == "中国新闻" for h in hits)

    def test_date_from_filter(self, conn: sqlite3.Connection) -> None:
        hits = search_news("华为", date_from="2024-01-15")
        assert all(h["pub_date"] >= "2024-01-15" for h in hits)

    def test_invalid_date_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            search_news("x", date_from="not-a-date")

    def test_and_mode(self, conn: sqlite3.Connection) -> None:
        assert len(search_news("世界苦茶 华为", mode="and")) == 2

    def test_phrase_mode(self, conn: sqlite3.Connection) -> None:
        assert search_news("世界苦茶 华为", mode="phrase") == []

    def test_connection_is_closed_after_success(self, conn: sqlite3.Connection) -> None:
        search_news("华为")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


class TestGetPost:
    def test_get_by_slug(self, conn: sqlite3.Connection) -> None:
        result = get_post("shijie-kucha-2024-01-15")
        assert result["slug"] == "shijie-kucha-2024-01-15"  # type: ignore[index]
        assert result["title"] == "世界苦茶 2024-01-15"  # type: ignore[index]
        assert result["pub_date"] == "2024-01-15"  # type: ignore[index]
        assert isinstance(result["items"], list)  # type: ignore[index]
        assert len(result["items"]) >= 1  # type: ignore[index]

    def test_get_by_date_with_multiple_returns_list(self, conn: sqlite3.Connection) -> None:
        result = get_post("2024-01-14")
        assert isinstance(result, list)
        assert len(result) >= 2
        slugs = {p["slug"] for p in result}
        assert "shijie-kucha-2024-01-14" in slugs
        assert "taiwan-alert-2024-01-14" in slugs

    def test_invalid_slug_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            get_post("../etc/passwd")

    def test_missing_post_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(KeyError):
            get_post("nonexistent-slug")

    def test_connection_is_closed_after_error(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            get_post("../etc/passwd")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


class TestListRecent:
    def test_returns_posts_within_days(self, conn: sqlite3.Connection) -> None:
        result = list_recent(days=7)
        assert len(result) >= 3

    def test_days_filter_excludes_old(self, conn: sqlite3.Connection) -> None:
        # All seeded posts Jan 14-15, today=Jan 16 → all within 2 days
        result = list_recent(days=2)
        assert len(result) >= 3


class TestDiscover:
    def test_by_query_title_substring(self, conn: sqlite3.Connection) -> None:
        results = discover(query="台海")
        assert len(results) >= 1
        assert "台海危機ALERT" in results[0]["title"]

    def test_by_post_type(self, conn: sqlite3.Connection) -> None:
        results = discover(post_type="台海危機ALERT")
        assert len(results) == 1
        assert results[0]["post_type"] == "台海危機ALERT"

    def test_by_date_range(self, conn: sqlite3.Connection) -> None:
        results = discover(date_from="2024-01-15")
        assert all(r["pub_date"] >= "2024-01-15" for r in results)

    def test_invalid_date_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            discover(date_from="bad")

    def test_reversed_date_range_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="date_from"):
            discover(date_from="2024-02-01", date_to="2024-01-01")
