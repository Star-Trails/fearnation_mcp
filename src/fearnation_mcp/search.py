"""FTS5 query builder + OpenCC t2s normalization.

OpenCC normalization is the single highest-ROI search quality lever:
the site mixes Simplified/Traditional Chinese (台海危機ALERT is Traditional-titled,
世界苦茶 bodies lean Simplified). We normalize both indexed content
(via db.upsert_items pre-computing _norm columns) and query strings
to Simplified before MATCH.

Additionally, since SQLite's ``unicode61`` tokenizer treats contiguous CJK
runs as a single whole-word token (so ``MATCH '稀土'`` silently returns 0 rows
against a doc ``稀土供应链进展``), ``normalize_text`` inserts a space between every
adjacent CJK character pair (and between CJK and a following ASCII alphanumeric).
Both index-time ``_norm`` column values and the query string pass through
``normalize_text``, so each CJK char becomes an adjacent single-token entry —
the implicit-phrase MATCH then picks up substring queries like ``"稀 土"``
against a doc normalized to ``"稀 土 供 应 链"``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Literal, cast

import opencc

from fearnation_mcp.utils import get_logger, validate_date_range

log = get_logger(__name__)

_converter: opencc.OpenCC | None = None

# CJK Unified Ideographs + Ext A + Compatibility ideographs + Kangxi radicals
# (covering all CJK chars that actually appear in FearNation content).
_CJK_RANGE = (
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\u3400-\u4dbf"  # CJK Extension A
    r"\uf900-\ufaf5"  # CJK Compatibility Ideographs (upper bound is real last char)
    r"\u2f00-\u2fdf"  # Kangxi Radicals
)
# Match any CJK char followed by another CJK char OR an ASCII alphanumeric;
# insert a space between them. We don't split CJK followed by space/punct
# (those already tokenize as boundaries).
_CJK_BOUNDARY_RE = re.compile(rf"([{_CJK_RANGE}])(?=[{_CJK_RANGE}A-Za-z0-9])")

SearchMode = Literal["and", "phrase"]


def _get_converter() -> opencc.OpenCC:
    global _converter
    if _converter is None:
        # opencc ships py.typed but OpenCC derives from an untyped C-extension
        # base (opencc_clib._OpenCC) — its member types are Unknown in strict
        # mode. Suppress the singleton-cache plumbing only.
        _converter = opencc.OpenCC("t2s")  # type: ignore[assignment]
    return _converter  # type: ignore[return-value]


def normalize_text(text: str) -> str:
    """Normalize text to Simplified Chinese with CJK char-splitting for FTS5.

    Two-stage: (1) OpenCC t2s converts Traditional → Simplified; (2) CJK char-split
    inserts a space between every adjacent CJK pair (and between CJK and following
    ASCII alphanumeric) so unicode61 tokenizer emits each CJK char as its own
    adjacent token, enabling substring phrase queries like ``MATCH '"华 为"'``
    against a doc whose normalized form is ``"华 为 新 闻"``.

    Used at index time (pre-compute _norm columns) and at query time
    (normalize query before MATCH).
    """
    if not text:
        return ""
    # opencc ships py.typed, but its `convert()` (and the C-extension base
    # class opencc_clib._OpenCC) lack return annotations — cast to str.
    simplified = cast(str, _get_converter().convert(text))
    return _CJK_BOUNDARY_RE.sub(r"\1 ", simplified)


@dataclass(frozen=True)
class SearchHit:
    slug: str
    section: str | None
    headline: str | None
    body_text: str | None
    pub_date: str | None
    seq: int | None
    rank: float


def _quote_fts_phrase(text: str) -> str:
    """Quote normalized text as a safe FTS5 phrase."""
    return f'"{text.replace(chr(34), chr(34) * 2)}"'


def _build_match_expression(query: str, mode: SearchMode) -> str:
    """Build a safe FTS5 expression for AND-keyword or exact-phrase search."""
    if mode not in ("and", "phrase"):
        raise ValueError(f"mode must be 'and' or 'phrase', got {mode!r}")

    if mode == "phrase":
        normalized = normalize_text(query).strip()
        return _quote_fts_phrase(normalized) if normalized else ""

    phrases = [
        _quote_fts_phrase(normalized)
        for term in query.split()
        if (normalized := normalize_text(term).strip())
    ]
    return " AND ".join(phrases)


def search_items(
    conn: sqlite3.Connection,
    query: str,
    section: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    mode: SearchMode = "and",
) -> list[SearchHit]:
    """Search indexed items by FTS5 + filters.

    Args:
        query: Free-text query (will be OpenCC-normalized before MATCH).
        section: Optional section filter (e.g. 中国新闻).
        date_from: Optional ISO date (inclusive).
        date_to: Optional ISO date (inclusive).
        limit: Max results to return (default 20, max 200).
        mode: ``and`` requires every whitespace-delimited keyword to match;
            ``phrase`` requires the complete query to appear as one phrase.
    """
    validate_date_range(date_from, date_to)
    if limit < 1 or limit > 200:
        raise ValueError(f"limit must be in [1, 200], got {limit}")

    match_expr = _build_match_expression(query, mode)
    if not match_expr:
        return []

    sql_parts: list[str] = [
        "SELECT items.post_slug AS slug, items.section, items.headline,",
        "       items.body_text, items.pub_date, items.seq,",
        "       items_fts.rank",
        "FROM items_fts",
        "JOIN items ON items.id = items_fts.rowid",
        "WHERE items_fts MATCH ?",
    ]
    params: list[object] = [match_expr]
    if section:
        sql_parts.append("AND items.section = ?")
        params.append(section)
    if date_from:
        sql_parts.append("AND items.pub_date >= ?")
        params.append(date_from)
    if date_to:
        sql_parts.append("AND items.pub_date <= ?")
        params.append(date_to)
    sql_parts.append("ORDER BY items_fts.rank LIMIT ?")
    params.append(limit)

    sql = "\n".join(sql_parts)
    rows = conn.execute(sql, params).fetchall()
    hits = [
        SearchHit(
            slug=r["slug"],
            section=r["section"],
            headline=r["headline"],
            body_text=r["body_text"],
            pub_date=r["pub_date"],
            seq=r["seq"],
            rank=r["rank"],
        )
        for r in rows
    ]
    log.info(
        "search executed",
        extra={
            "query": query,
            "mode": mode,
            "section": section,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "result_count": len(hits),
        },
    )
    return hits
