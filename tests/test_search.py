"""Tests for search.py: FTS5 query builder, OpenCC normalization."""

from __future__ import annotations

import sqlite3

import pytest

from fearnation_mcp.db import ItemRow, PostRow, init_schema, upsert_items, upsert_post
from fearnation_mcp.search import normalize_text, search_items


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


class TestNormalizeText:
    def test_traditional_chinese_to_simplified(self) -> None:
        # OpenCC t2s converts 稀土開採 → 稀土开采, then char-splits the CJK run
        assert normalize_text("稀土開採") == "稀 土 开 采"

    def test_already_simplified_gets_char_split(self) -> None:
        # Already-simplified input still needs CJK char-split for FTS5 to work
        assert normalize_text("稀土开采") == "稀 土 开 采"

    def test_mixed_keeps_non_cjk(self) -> None:
        result = normalize_text("USD 稀土開採 7.2")
        assert "USD" in result  # ASCII run untouched
        assert "稀 土 开 采" in result  # OpenCC + char-split applied
        assert "7.2" in result  # digits untouched

    def test_single_cjk_char_unchanged(self) -> None:
        # Single CJK char has no adjacent CJK to split against — no space inserted
        assert normalize_text("稀") == "稀"

    def test_cjk_followed_by_digit_gets_space(self) -> None:
        # CJK followed by ASCII digit needs space so digit tokenizes separately
        assert normalize_text("正文A") == "正 文 A"
        assert normalize_text("汇率7") == "汇 率 7"

    def test_empty_string(self) -> None:
        assert normalize_text("") == ""


def _seed_post(
    conn: sqlite3.Connection,
    slug: str = "s",
    title: str = "测试",
    pub_date: str = "2024-01-15",
    items: list[tuple[str, str, str]] | None = None,
) -> None:
    items = items or [("产业新闻", "稀土供应链出现新进展", "正文A")]
    upsert_post(
        conn,
        PostRow(
            slug=slug,
            title=title,
            pub_date=pub_date,
            post_type="世界苦茶",
            raw_html="x",
        ),
    )
    item_rows = [
        ItemRow(
            section=s,
            headline=h,
            headline_norm=normalize_text(h),
            body_text=b,
            body_norm=normalize_text(b),
            seq=i,
            pub_date=pub_date,
        )
        for i, (s, h, b) in enumerate(items)
    ]
    upsert_items(conn, slug, item_rows, pub_date=pub_date)


class TestSearchItems:
    def test_basic_match(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn)
        hits = search_items(conn, "稀土")
        assert len(hits) == 1
        assert hits[0].headline == "稀土供应链出现新进展"
        assert hits[0].pub_date == "2024-01-15"
        assert hits[0].slug == "s"

    def test_cross_script_simplified_query_finds_traditional_content(
        self, conn: sqlite3.Connection
    ) -> None:
        # Seed with Traditional Chinese content (稀土開採)
        _seed_post(conn, items=[("产业新闻", "稀土開採政策更新", "正文")])
        # Query with Simplified Chinese 稀土开采 → must find Traditional content
        hits = search_items(conn, "稀土开采")
        assert len(hits) >= 1
        assert "稀土開採" in hits[0].headline or "稀土开采" in hits[0].headline

    def test_cross_script_traditional_query_finds_simplified_content(
        self, conn: sqlite3.Connection
    ) -> None:
        _seed_post(conn, items=[("产业新闻", "稀土开采政策更新", "正文")])
        # Query with Traditional Chinese 稀土開採 → must find Simplified content
        hits = search_items(conn, "稀土開採")
        assert len(hits) == 1

    def test_and_mode_matches_keywords_in_different_parts(self, conn: sqlite3.Connection) -> None:
        _seed_post(
            conn,
            items=[("产业新闻", "稀土供应链调整", "下游制造业随后回应")],
        )
        hits = search_items(conn, "稀土 制造业", mode="and")
        assert len(hits) == 1

    def test_phrase_mode_requires_adjacent_terms(self, conn: sqlite3.Connection) -> None:
        _seed_post(
            conn,
            items=[("产业新闻", "稀土供应链调整", "下游制造业随后回应")],
        )
        assert search_items(conn, "稀土 制造业", mode="phrase") == []

        _seed_post(
            conn,
            slug="exact",
            items=[("产业新闻", "稀土 制造业协同项目", "正文")],
        )
        hits = search_items(conn, "稀土 制造业", mode="phrase")
        assert [hit.slug for hit in hits] == ["exact"]

    def test_default_mode_is_and(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn, items=[("产业新闻", "稀土供应链调整", "下游制造业回应")])
        assert len(search_items(conn, "稀土 制造业")) == 1

    def test_invalid_mode_raises(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn)
        with pytest.raises(ValueError, match="mode must be"):
            search_items(conn, "稀土", mode="or")  # type: ignore[arg-type]

    def test_section_filter(self, conn: sqlite3.Connection) -> None:
        _seed_post(
            conn,
            items=[
                ("中国新闻", "标题A", "正文A"),
                ("印太新闻", "标题B", "正文B"),
            ],
        )
        hits = search_items(conn, "标题", section="印太新闻")
        assert len(hits) == 1
        assert hits[0].headline == "标题B"

    def test_date_range_filter(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn, slug="p1", pub_date="2024-01-10")
        _seed_post(conn, slug="p2", pub_date="2024-02-10", items=[("产业新闻", "稀土X", "y")])
        hits = search_items(conn, "稀土", date_from="2024-02-01")
        assert len(hits) == 1
        assert hits[0].slug == "p2"

    def test_reversed_date_range_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="date_from"):
            search_items(conn, "稀土", date_from="2024-02-01", date_to="2024-01-01")

    def test_limit(self, conn: sqlite3.Connection) -> None:
        items = [("新闻", f"标题{i}", "正文") for i in range(30)]
        _seed_post(conn, items=items)
        hits = search_items(conn, "标题", limit=5)
        assert len(hits) == 5

    def test_no_results_returns_empty(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn)
        assert search_items(conn, "不存在的关键词") == []
