"""Tests for crawler.py: sitemap recursion, RSS parse, crawl flow."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from fearnation_mcp.crawler import (
    fetch_url,
    parse_rss,
    parse_sitemap,
)
from fearnation_mcp.db import PostRow, get_connection, get_meta, init_schema, upsert_post

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseSitemap:
    def test_parse_urlset(self) -> None:
        xml = (FIXTURES / "sitemap-posts.xml").read_text(encoding="utf-8")
        entries = parse_sitemap(xml)
        assert len(entries) == 3
        assert entries[0].loc == "https://fearnation.club/shijie-kucha-2024-01-15/"
        assert entries[0].lastmod == "2024-01-15T08:00:00+00:00"
        assert not entries[0].is_sitemap

    def test_parse_sitemapindex_returns_entries_to_recurse(self) -> None:
        xml = (FIXTURES / "sitemap-index.xml").read_text(encoding="utf-8")
        entries = parse_sitemap(xml)
        assert len(entries) == 2
        assert entries[0].loc == "https://fearnation.club/sitemap-posts.xml"
        assert entries[0].is_sitemap

    def test_parse_empty(self) -> None:
        assert parse_sitemap("") == []

    def test_malformed_xml_returns_empty(self) -> None:
        assert parse_sitemap("<not xml<<<") == []


class TestParseRss:
    def test_parse_rss_extracts_entries(self) -> None:
        xml = (FIXTURES / "rss.xml").read_text(encoding="utf-8")
        items = parse_rss(xml)
        assert len(items) == 2
        first = items[0]
        assert first.title == "世界苦茶 2024-01-15"
        assert first.slug == "shijie-kucha-2024-01-15"
        assert first.pub_date == "2024-01-15"
        assert "<h1>苦茶数据</h1>" in first.content_html

    def test_slug_extracted_from_link(self) -> None:
        xml = (FIXTURES / "rss.xml").read_text(encoding="utf-8")
        items = parse_rss(xml)
        assert items[1].slug == "taiwan-alert-2024-01-14"

    def test_parse_rss_empty(self) -> None:
        assert parse_rss("") == []

    def test_off_site_entry_is_skipped(self) -> None:
        rss = """<rss><channel><item>
        <title>Untrusted</title><link>https://evil.example/post/</link>
        </item></channel></rss>"""
        assert parse_rss(rss) == []


class _MockResp:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=None, response=self)  # type: ignore[arg-type]


class _MockClient:
    """Mock httpx.Client returning fixture content by URL suffix."""

    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes
        self.call_count = 0
        self.calls: list[str] = []

    def get(self, url: str, timeout: float = 15.0) -> _MockResp:
        self.call_count += 1
        self.calls.append(url)
        for suffix, content in self.routes.items():
            if url.endswith(suffix) or url.rstrip("/").endswith(suffix.rstrip("/")):
                return _MockResp(200, content)
        return _MockResp(404, "")


class TestFetchUrl:
    def test_success(self) -> None:
        client = _MockClient({"/x/": "<html>hello</html>"})
        assert fetch_url(client, "https://fearnation.club/x/") == "<html>hello</html>"

    def test_raises_on_non_2xx(self) -> None:
        client = _MockClient({})  # all 404
        with pytest.raises(httpx.HTTPError):
            fetch_url(client, "https://fearnation.club/x/")


from fearnation_mcp.crawler import crawl_all, refresh_rss  # noqa: E402


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


class TestCrawlAll:
    def test_full_crawl_indexes_all_posts(self, conn: sqlite3.Connection) -> None:
        client = _MockClient(
            {
                "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(encoding="utf-8"),
                "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(encoding="utf-8"),
                "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(
                    encoding="utf-8"
                ),
                "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(
                    encoding="utf-8"
                ),
                "/old-post-2020-01-01/": (
                    "<html><body><main class='post-content'>"
                    "<h1>新闻</h1><p><strong>• 老</strong><br>内容</p>"
                    "</main></body></html>"
                ),
            }
        )
        report = crawl_all(client, conn, rate_limit_sec=0)
        assert report.posts_fetched == 3
        assert report.posts_failed == 0
        slugs = {r["slug"] for r in conn.execute("SELECT slug FROM posts").fetchall()}
        assert "shijie-kucha-2024-01-15" in slugs
        assert "taiwan-alert-2024-01-14" in slugs
        assert "old-post-2020-01-01" in slugs
        items_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert items_count >= 4
        assert get_meta(conn, "full_crawl_done") is not None
        assert report.financial_rows > 0
        assert report.completed

    def test_off_site_child_sitemap_is_not_fetched(self, conn: sqlite3.Connection) -> None:
        client = _MockClient(
            {
                "/sitemap.xml": (
                    "<sitemapindex><sitemap>"
                    "<loc>https://evil.example/private.xml</loc>"
                    "</sitemap></sitemapindex>"
                )
            }
        )
        report = crawl_all(client, conn, rate_limit_sec=0)
        assert report.completed
        assert "https://evil.example/private.xml" not in client.calls

    def test_root_sitemap_failure_is_not_completed(self, conn: sqlite3.Connection) -> None:
        report = crawl_all(_MockClient({}), conn, rate_limit_sec=0)
        assert not report.completed
        assert get_meta(conn, "full_crawl_done") is None

    def test_full_crawl_metadata_persists_after_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "crawl.db"
        disk_conn = get_connection(db_path)
        client = _MockClient(
            {"/sitemap.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />'}
        )

        crawl_all(client, disk_conn, rate_limit_sec=0)
        disk_conn.close()

        reopened = get_connection(db_path)
        try:
            assert get_meta(reopened, "full_crawl_done") is not None
        finally:
            reopened.close()

    def test_idempotent_skip_when_lastmod_unchanged(self, conn: sqlite3.Connection) -> None:
        client = _MockClient(
            {
                "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(encoding="utf-8"),
                "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(encoding="utf-8"),
                "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(
                    encoding="utf-8"
                ),
                "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(
                    encoding="utf-8"
                ),
                "/old-post-2020-01-01/": (
                    "<html><body><main class='post-content'>"
                    "<h1>x</h1><p><strong>• y</strong><br>z</p>"
                    "</main></body></html>"
                ),
            }
        )
        crawl_all(client, conn, rate_limit_sec=0)
        # Second crawl with unchanged lastmod → 0 new posts fetched
        report = crawl_all(client, conn, rate_limit_sec=0)
        assert report.posts_fetched == 0
        # Posts in DB still all 3
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 3

    def test_retry_on_failure_then_success(self, conn: sqlite3.Connection) -> None:
        class _FlakyClient(_MockClient):
            def __init__(self, routes: dict[str, str], fail_once_for: str) -> None:
                super().__init__(routes)
                self.fail_once_for = fail_once_for
                self._failed = False

            def get(self, url: str, timeout: float = 15.0) -> _MockResp:
                if self.fail_once_for in url and not self._failed:
                    self._failed = True
                    return _MockResp(503, "")
                return super().get(url, timeout)

        client = _FlakyClient(
            {
                "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(encoding="utf-8"),
                "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(encoding="utf-8"),
                "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(
                    encoding="utf-8"
                ),
                "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(
                    encoding="utf-8"
                ),
                "/old-post-2020-01-01/": "<html><body>ok</body></html>",
            },
            fail_once_for="/taiwan-alert-2024-01-14/",
        )
        report = crawl_all(client, conn, rate_limit_sec=0, max_retries=3)
        assert report.posts_failed == 0
        assert report.posts_fetched == 3


class TestRefreshRss:
    def test_refresh_indexes_new_posts(self, conn: sqlite3.Connection) -> None:
        client = _MockClient(
            {
                "/rss/": (FIXTURES / "rss.xml").read_text(encoding="utf-8"),
            }
        )
        count = refresh_rss(client, conn)
        assert count == 2
        slugs = {r["slug"] for r in conn.execute("SELECT slug FROM posts").fetchall()}
        assert "shijie-kucha-2024-01-15" in slugs
        assert "taiwan-alert-2024-01-14" in slugs
        assert get_meta(conn, "last_rss_fetch") is not None

    def test_refresh_metadata_persists_after_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "rss.db"
        disk_conn = get_connection(db_path)
        client = _MockClient({"/rss/": (FIXTURES / "rss.xml").read_text(encoding="utf-8")})

        refresh_rss(client, disk_conn)
        disk_conn.close()

        reopened = get_connection(db_path)
        try:
            assert get_meta(reopened, "last_rss_fetch") is not None
        finally:
            reopened.close()

    def test_refresh_fetch_failure_returns_zero(self, conn: sqlite3.Connection) -> None:
        client = _MockClient({})  # all 404
        assert refresh_rss(client, conn) == 0

    def test_refresh_preserves_sitemap_metadata_and_stores_reparseable_html(
        self, conn: sqlite3.Connection
    ) -> None:
        upsert_post(
            conn,
            PostRow(
                slug="shijie-kucha-2024-01-15",
                title="Existing",
                pub_date="2024-01-15",
                post_type="世界苦茶",
                raw_html="<html>existing</html>",
                lastmod="2024-01-15T08:00:00+00:00",
            ),
        )
        client = _MockClient({"/rss/": (FIXTURES / "rss.xml").read_text(encoding="utf-8")})

        refresh_rss(client, conn)

        row = conn.execute(
            "SELECT raw_html, lastmod FROM posts WHERE slug='shijie-kucha-2024-01-15'"
        ).fetchone()
        assert row["lastmod"] == "2024-01-15T08:00:00+00:00"
        assert "<!DOCTYPE html>" in row["raw_html"]
        assert "<title>世界苦茶 2024-01-15</title>" in row["raw_html"]
