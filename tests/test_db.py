# tests/test_db.py
"""Tests for db.py: schema init, upserts, idempotency."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from fearnation_mcp.db import (
    FinancialDataRow,
    ItemRow,
    PostRow,
    get_meta,
    init_schema,
    set_meta,
    upsert_financial_data,
    upsert_items,
    upsert_post,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


class TestSchema:
    def test_tables_exist(self, conn: sqlite3.Connection) -> None:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"posts", "items", "financial_data", "items_fts", "meta"} <= tables

    def test_items_fts_is_plain_not_external(self, conn: sqlite3.Connection) -> None:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='items_fts'").fetchone()
        assert sql is not None
        assert "content=" not in sql["sql"].lower()

    def test_items_pub_date_index(self, conn: sqlite3.Connection) -> None:
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_items_pub_date" in indexes


class TestMeta:
    def test_set_get_roundtrip(self, conn: sqlite3.Connection) -> None:
        set_meta(conn, "last_rss_fetch", "2024-01-01T00:00:00Z")
        assert get_meta(conn, "last_rss_fetch") == "2024-01-01T00:00:00Z"

    def test_get_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        assert get_meta(conn, "nonexistent") is None

    def test_set_overwrites(self, conn: sqlite3.Connection) -> None:
        set_meta(conn, "k", "v1")
        set_meta(conn, "k", "v2")
        assert get_meta(conn, "k") == "v2"


def _make_post(slug: str = "test-slug") -> PostRow:
    return PostRow(
        slug=slug,
        title="测试标题",
        pub_date="2024-01-15",
        post_type="世界苦茶",
        raw_html="<p>raw</p>",
        lastmod="2024-01-15T00:00:00Z",
    )


class TestPostUpsert:
    def test_insert_then_query(self, conn: sqlite3.Connection) -> None:
        post = _make_post()
        upsert_post(conn, post)
        row = conn.execute("SELECT * FROM posts WHERE slug=?", (post.slug,)).fetchone()
        assert row["title"] == "测试标题"
        assert row["pub_date"] == "2024-01-15"

    def test_upsert_is_idempotent(self, conn: sqlite3.Connection) -> None:
        post = _make_post()
        upsert_post(conn, post)
        post2 = dataclasses.replace(post, title="新标题")
        upsert_post(conn, post2)
        rows = conn.execute("SELECT * FROM posts WHERE slug=?", (post.slug,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "新标题"

    def test_partial_update_preserves_existing_metadata(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, _make_post())
        upsert_post(
            conn,
            PostRow(
                slug="test-slug",
                title="",
                pub_date=None,
                post_type=None,
                raw_html="",
                lastmod=None,
            ),
        )
        row = conn.execute("SELECT * FROM posts WHERE slug='test-slug'").fetchone()
        assert row["title"] == "测试标题"
        assert row["pub_date"] == "2024-01-15"
        assert row["post_type"] == "世界苦茶"
        assert row["raw_html"] == "<p>raw</p>"
        assert row["lastmod"] == "2024-01-15T00:00:00Z"


def _make_items(slug: str, pub_date: str = "2024-01-15") -> list[ItemRow]:
    return [
        ItemRow(
            section="中国新闻",
            headline="标题A",
            headline_norm="标 题 A",
            body_text="正文A",
            body_norm="正 文 A",
            seq=0,
            pub_date=pub_date,
        ),
        ItemRow(
            section="中国新闻",
            headline="标题B",
            headline_norm="标 题 B",
            body_text="正文B",
            body_norm="正 文 B",
            seq=1,
            pub_date=pub_date,
        ),
    ]


class TestItemsUpsert:
    def test_inserts_items(self, conn: sqlite3.Connection) -> None:
        upsert_post(
            conn,
            PostRow(slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"),
        )
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        rows = conn.execute("SELECT * FROM items WHERE post_slug=? ORDER BY seq", ("s",)).fetchall()
        assert len(rows) == 2
        assert rows[0]["headline"] == "标题A"
        assert rows[0]["pub_date"] == "2024-01-15"

    def test_replaces_on_reupsert(self, conn: sqlite3.Connection) -> None:
        upsert_post(
            conn,
            PostRow(slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"),
        )
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        upsert_items(conn, "s", _make_items("s")[:1], pub_date="2024-01-15")
        rows = conn.execute("SELECT * FROM items WHERE post_slug=?", ("s",)).fetchall()
        assert len(rows) == 1

    def test_fts_indexed(self, conn: sqlite3.Connection) -> None:
        upsert_post(
            conn,
            PostRow(slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"),
        )
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        rows = conn.execute(
            "SELECT items.headline FROM items_fts "
            "JOIN items ON items.id = items_fts.rowid "
            "WHERE items_fts.body_norm MATCH '\"正 文\"'"
        ).fetchall()
        assert len(rows) == 2

    def test_fts_clears_on_reupsert(self, conn: sqlite3.Connection) -> None:
        upsert_post(
            conn,
            PostRow(slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"),
        )
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        upsert_items(conn, "s", _make_items("s")[:1], pub_date="2024-01-15")
        rows = conn.execute(
            "SELECT items.headline FROM items_fts "
            "JOIN items ON items.id = items_fts.rowid "
            "WHERE items_fts.body_norm MATCH '\"正 文\"'"
        ).fetchall()
        assert len(rows) == 1


class TestFinancialDataUpsert:
    def test_inserts_and_replaces(self, conn: sqlite3.Connection) -> None:
        upsert_post(
            conn,
            PostRow(slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"),
        )
        upsert_financial_data(
            conn,
            "s",
            [
                FinancialDataRow(field="USD/CNH", value="7.2"),
                FinancialDataRow(field="BTC", value="$42000"),
            ],
        )
        rows = conn.execute(
            "SELECT * FROM financial_data WHERE post_slug=? ORDER BY field", ("s",)
        ).fetchall()
        assert len(rows) == 2

        upsert_financial_data(
            conn,
            "s",
            [
                FinancialDataRow(field="USD/CNH", value="7.3"),
            ],
        )
        rows = conn.execute("SELECT * FROM financial_data WHERE post_slug=?", ("s",)).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == "7.3"


class TestFullRoundtrip:
    def test_double_upsert_yields_no_duplicates(self, conn: sqlite3.Connection) -> None:
        post = PostRow(
            slug="s", title="t", pub_date="2024-01-15", post_type="世界苦茶", raw_html="x"
        )
        items = [
            ItemRow(
                section="x",
                headline="h1",
                headline_norm="h1",
                body_text="b1",
                body_norm="b1",
                seq=0,
                pub_date="2024-01-15",
            ),
            ItemRow(
                section="x",
                headline="h2",
                headline_norm="h2",
                body_text="b2",
                body_norm="b2",
                seq=1,
                pub_date="2024-01-15",
            ),
        ]
        fin = [FinancialDataRow(field="USD/CNH", value="7.2")]

        upsert_post(conn, post)
        upsert_items(conn, "s", items, pub_date="2024-01-15")
        upsert_financial_data(conn, "s", fin)

        # Re-run
        upsert_post(conn, post)
        upsert_items(conn, "s", items, pub_date="2024-01-15")
        upsert_financial_data(conn, "s", fin)

        assert conn.execute("SELECT COUNT(*) FROM posts WHERE slug='s'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM items WHERE post_slug='s'").fetchone()[0] == 2
        assert (
            conn.execute("SELECT COUNT(*) FROM financial_data WHERE post_slug='s'").fetchone()[0]
            == 1
        )

        fts_count = conn.execute(
            "SELECT COUNT(*) FROM items_fts JOIN items ON items.id = items_fts.rowid "
            "WHERE items.post_slug='s'"
        ).fetchone()[0]
        assert fts_count == 2  # not 4
