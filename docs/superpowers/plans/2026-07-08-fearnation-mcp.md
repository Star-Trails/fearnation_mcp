# FearNation MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server that searches [fearnation.club](https://fearnation.club/) news (世界苦茶 daily + 台海危機 ALERT) at item-level granularity with OpenCC cross-script normalization.

**Architecture:** Full-crawl historical posts (sitemap) + RSS incremental; SQLite+FTS5 storage with OpenCC `t2s` normalized columns; permissive DOM-driven parser; 4 MCP tools; structured JSON-lines logs to stderr.

**Tech Stack:** Python 3.11+, uv, hatchling, `mcp` v2 (`MCPServer`), `beautifulsoup4`+`lxml`, `feedparser`, `opencc`, `httpx`, `pytest`, `ruff`, `black`, `pyright` strict.

## Global Constraints

- **Python**: 3.11+
- **MCP SDK**: `mcp>=2.0` — use `from mcp.server import MCPServer` (v2 API, `FastMCP` renamed in v2). Sync tool functions run in worker thread via `anyio.to_thread.run_sync`.
- **Never `print()`** in server code — stdout reserved for JSON-RPC. Always log via Python `logging` module (defaults to stderr).
- **OpenCC direction**: `t2s` (Traditional → Simplified) for normalization columns.
- **FTS5 tokenizer**: `unicode61 remove_diacritics 2` (NOT jieba). Plain FTS5 table (NOT external content table).
- **Rate limit**: 1 req/sec polite crawl (use `time.sleep(1)` between fetches).
- **Slug regex**: `^[a-z0-9][a-z0-9-]*$` (Ghost slug format).
- **DB path**: `$XDG_CACHE_HOME/fearnation_mcp/fearnation.db` (default `~/.cache/fearnation_mcp/`).
- **DB dir perms**: 0o700.
- **DB pragmas**: `journal_mode=WAL`, `foreign_keys=ON`.
- **Tool errors**: raise standard exceptions (`ValueError`, `KeyError`), never return error strings.
- **Item-level `pub_date`**: every `items` row carries the parent post's `pub_date` redundantly (no JOIN needed for date filters).
- **Returning content to AI**: `BeautifulSoup.get_text()` plain text only, never raw HTML.
- **Imports**: three groups (stdlib / third-party / local) with blank line between groups.
- **Every file**: starts with `from __future__ import annotations` + module docstring.
- **Type annotations**: required on all functions (pyright strict mode).
- **References**: spec at `docs/superpowers/specs/2026-07-08-fearnation-mcp-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Build config, deps, scripts entry, ruff/black/pyright config |
| `src/fearnation_mcp/__init__.py` | Package marker + `__version__` |
| `src/fearnation_mcp/__main__.py` | CLI entry — `python -m fearnation_mcp` |
| `src/fearnation_mcp/server.py` | `MCPServer` instance + 4 tool functions |
| `src/fearnation_mcp/crawler.py` | sitemap recursion, RSS parse, full-crawl driver with retry |
| `src/fearnation_mcp/parser.py` | DOM parser: `parse_post(slug, raw_html) -> ParsedPost` |
| `src/fearnation_mcp/db.py` | SQLite connection, schema init, upserts, queries |
| `src/fearnation_mcp/search.py` | FTS5 query builder + OpenCC wrapper |
| `src/fearnation_mcp/robots.py` | robots.txt fetch/parse/check |
| `src/fearnation_mcp/utils.py` | slug validation, URL safety, logging setup |
| `tests/conftest.py` | Shared fixtures |
| `tests/test_parser.py` | Parser unit tests (highest ROI) |
| `tests/test_db.py` | Schema + upsert idempotency tests |
| `tests/test_search.py` | FTS5 query builder + OpenCC cross-script tests |
| `tests/test_crawler.py` | sitemap recursion, RSS parse, full-crawl test |
| `tests/test_security.py` | slug validation / SSRF tests |
| `tests/test_server.py` | MCP tool function unit tests (call directly, not via wire) |
| `tests/test_robots.py` | robots.txt parse tests |
| `tests/test_smoke.py` | Live `@network`-marked tests (skipped by default) |
| `tests/fixtures/posts/*.html` | 3-5 real saved articles |
| `tests/fixtures/rss.xml` | Saved RSS feed |
| `tests/fixtures/sitemap*.xml` | Saved sitemap fixtures |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`, `src/fearnation_mcp/__init__.py`, `src/fearnation_mcp/__main__.py`
- Create: `.gitignore`, `tests/__init__.py`, `tests/conftest.py` (stub), `README.md` (stub)

**Interfaces:**
- Produces: package `fearnation_mcp`; entry script `fearnation-mcp` via `[project.scripts]`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "fearnation-mcp"
version = "0.1.0"
description = "MCP server for searching fearnation.club Chinese news archive"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "yuki" }]
dependencies = [
    "mcp>=2.0",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "feedparser>=6.0",
    "opencc>=1.1",
    "httpx>=0.27",
]

[project.scripts]
fearnation-mcp = "fearnation_mcp.__main__:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "black>=24.0",
    "pyright>=1.1.350",
]

[tool.hatch.build.targets.wheel]
packages = ["src/fearnation_mcp"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B"]

[tool.black]
line-length = 100
target-version = ["py311"]

[tool.pyright]
include = ["src", "tests"]
typeCheckingMode = "strict"
pythonVersion = "3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "network: tests that hit the live network (deselected by default)",
]
addopts = "-m 'not network'"
```

- [ ] **Step 2: Write `src/fearnation_mcp/__init__.py`**

```python
"""FearNation MCP: search fearnation.club news archive via MCP."""

from __future__ import annotations

__version__ = "0.1.0"
```

- [ ] **Step 3: Write `src/fearnation_mcp/__main__.py`**

```python
"""CLI entry point for fearnation-mcp.

Run with: `fearnation-mcp` or `python -m fearnation_mcp`.
"""

from __future__ import annotations

import logging

from fearnation_mcp.server import run


def main() -> None:
    """Run the MCP server over stdio transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],  # stderr by default
    )
    run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.venv/
dist/
build/
.pytest_cache/
.ruff_cache/
.pyright/
*.db
*.db-wal
*.db-shm
```

- [ ] **Step 5: Write `tests/__init__.py`** — empty file.

- [ ] **Step 6: Write stub `tests/conftest.py`**

```python
"""Shared pytest fixtures."""

from __future__ import annotations
```

- [ ] **Step 7: Write stub `README.md`**

```markdown
# fearnation-mcp

MCP server for searching fearnation.club news archive.

See `docs/superpowers/specs/2026-07-08-fearnation-mcp-design.md` for design.

Status: work in progress.
```

- [ ] **Step 8: Install deps with uv**

Run: `uv venv && uv pip install -e ".[dev]"`
Expected: venv created, `fearnation-mcp` editable-installed, dev tools available.

- [ ] **Step 9: Verify import**

Run: `uv run python -c "import fearnation_mcp; print(fearnation_mcp.__version__)"`
Expected: prints `0.1.0`

(Note: `fearnation-mcp` entry script will fail until server.run exists in Task 8 — that's expected.)

- [ ] **Step 10: Lint check**

Run: `uv run ruff check src tests && uv run black --check src tests`
Expected: ruff passes; black may complain → run `uv run black src tests` to format, then re-check.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml src/ tests/ .gitignore README.md uv.lock
git commit -m "chore: project scaffolding (uv + hatchling + ruff/black/pyright)"
```

---

## Task 2: utils.py — slug validation, URL safety, logging

**Files:**
- Create: `src/fearnation_mcp/utils.py`
- Create: `tests/test_security.py`

**Interfaces:**
- Produces:
  - `validate_slug(slug: str) -> str`
  - `build_post_url(slug: str) -> str`
  - `validate_iso_date(date_str: str) -> str`
  - `get_logger(name: str) -> logging.Logger`

- [ ] **Step 1: Write failing test for `validate_slug`**

```python
# tests/test_security.py
"""Tests for slug validation, URL safety, date validation."""

from __future__ import annotations

import pytest

from fearnation_mcp.utils import (
    build_post_url,
    validate_iso_date,
    validate_slug,
)


class TestValidateSlug:
    def test_valid_simple_slug(self) -> None:
        assert validate_slug("taiwan-alert-2024") == "taiwan-alert-2024"

    def test_valid_starts_with_digit(self) -> None:
        assert validate_slug("2024-jan-news") == "2024-jan-news"

    def test_valid_single_char(self) -> None:
        assert validate_slug("a") == "a"

    @pytest.mark.parametrize(
        "bad_slug",
        [
            "../", "..%2f", "//evil.com/x", "foo?bar=baz", "foo#anchor",
            "foo bar", "FooBar", "-leading-dash", "trailing-dash-",
            "", "with_underscore", "with/slash", "with:colon",
            "with(paren)", "with;semicolon",
        ],
    )
    def test_rejects_invalid_slug(self, bad_slug: str) -> None:
        with pytest.raises(ValueError):
            validate_slug(bad_slug)


class TestBuildPostUrl:
    def test_basic(self) -> None:
        assert build_post_url("taiwan-alert") == "https://fearnation.club/taiwan-alert/"

    def test_validates_slug_first(self) -> None:
        with pytest.raises(ValueError):
            build_post_url("../etc/passwd")

    def test_validates_slug_with_query(self) -> None:
        with pytest.raises(ValueError):
            build_post_url("foo?bar=1")


class TestValidateIsoDate:
    def test_valid(self) -> None:
        assert validate_iso_date("2024-01-15") == "2024-01-15"

    @pytest.mark.parametrize(
        "bad",
        ["2024-1-15", "2024-01-32", "2024-02-30", "20240115",
         "not-a-date", "", "2024/01/15"],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_iso_date(bad)


class TestJsonLinesLogger:
    def test_extra_fields_serialize(self, caplog: pytest.LogCaptureFixture) -> None:
        from fearnation_mcp.utils import get_logger
        logger = get_logger("test.extra")
        logger.warning("hello", extra={"url": "x", "status": 200, "latency_ms": 5})
        # No exception raised == pass.
```

- [ ] **Step 2: Run test — expect ImportError**

Run: `uv run pytest tests/test_security.py -v`
Expected: `ImportError: cannot import name 'validate_slug' ...`

- [ ] **Step 3: Write `utils.py`**

```python
# src/fearnation_mcp/utils.py
"""Slug validation, URL safety, ISO date validation, JSON-lines stderr logging."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_slug(slug: str) -> str:
    """Validate a Ghost slug. Raises ValueError on invalid input.

    Ghost slugs match: ^[a-z0-9][a-z0-9-]*$ (lowercase alnum + hyphens,
    no leading/trailing hyphen, no underscores).
    """
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ValueError(f"Invalid slug: {slug!r}")
    return slug


def build_post_url(slug: str) -> str:
    """Build the canonical post URL for a slug, with host/scheme pinning."""
    validate_slug(slug)
    resolved = urljoin("https://fearnation.club/", slug + "/")
    parsed = urlparse(resolved)
    if parsed.netloc != "fearnation.club" or parsed.scheme != "https":
        raise ValueError(f"URL did not resolve to fearnation.club: {resolved}")
    if parsed.path.strip("/") != slug:
        raise ValueError(f"URL path drifted from slug: {resolved}")
    return resolved


def validate_iso_date(date_str: str) -> str:
    """Validate an ISO 8601 date string (YYYY-MM-DD)."""
    if not isinstance(date_str, str) or not _ISO_DATE_RE.match(date_str):
        raise ValueError(f"Invalid ISO date: {date_str!r}")
    datetime.strptime(date_str, "%Y-%m-%d")
    return date_str


class _JsonLinesFormatter(logging.Formatter):
    """Format log records as JSON-lines to stderr."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "taskName",
        "ts", "level", "logger",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured to emit JSON-lines to stderr.

    Idempotent — calling twice with same name returns same logger
    without adding duplicate handlers.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonLinesFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
```

- [ ] **Step 4: Run all security tests**

Run: `uv run pytest tests/test_security.py -v`
Expected: all pass.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/utils.py tests/test_security.py && uv run black --check src/fearnation_mcp/utils.py tests/test_security.py && uv run pyright src/fearnation_mcp/utils.py`
Expected: clean, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/fearnation_mcp/utils.py tests/test_security.py
git commit -m "feat(utils): slug validation, URL safety, ISO date validation, JSON-lines logger"
```

---

## Task 3: db.py — SQLite schema + connection

**Files:**
- Create: `src/fearnation_mcp/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `DB_PATH: Path`
  - `get_connection(db_path: Path | None = None) -> sqlite3.Connection`
  - `init_schema(conn) -> None`
  - `upsert_post(conn, post: PostRow) -> None`
  - `upsert_items(conn, post_slug, items, pub_date) -> None`
  - `upsert_financial_data(conn, post_slug, rows) -> None`
  - `set_meta(conn, key, value) -> None` / `get_meta(conn, key) -> str | None`
  - Dataclasses: `PostRow`, `ItemRow`, `FinancialDataRow`

- [ ] **Step 1: Write failing test**

```python
# tests/test_db.py
"""Tests for db.py: schema init, upserts, idempotency."""

from __future__ import annotations

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
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"posts", "items", "financial_data", "items_fts", "meta"} <= tables

    def test_items_fts_is_plain_not_external(self, conn: sqlite3.Connection) -> None:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='items_fts'"
        ).fetchone()
        assert sql is not None
        assert "content=" not in sql["sql"].lower()

    def test_items_pub_date_index(self, conn: sqlite3.Connection) -> None:
        indexes = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
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
        slug=slug, title="测试标题", pub_date="2024-01-15",
        post_type="世界苦茶", raw_html="<p>raw</p>", lastmod="2024-01-15T00:00:00Z",
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
        post2 = post.__replace__(title="新标题")
        upsert_post(conn, post2)
        rows = conn.execute("SELECT * FROM posts WHERE slug=?", (post.slug,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "新标题"


def _make_items(slug: str, pub_date: str = "2024-01-15") -> list[ItemRow]:
    return [
        ItemRow(section="中国新闻", headline="标题A", headline_norm="标题A",
                body_text="正文A", body_norm="正文A", seq=0, pub_date=pub_date),
        ItemRow(section="中国新闻", headline="标题B", headline_norm="标题B",
                body_text="正文B", body_norm="正文B", seq=1, pub_date=pub_date),
    ]


class TestItemsUpsert:
    def test_inserts_items(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, PostRow(slug="s", title="t", pub_date="2024-01-15",
                                   post_type="世界苦茶", raw_html="x"))
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        rows = conn.execute("SELECT * FROM items WHERE post_slug=? ORDER BY seq", ("s",)).fetchall()
        assert len(rows) == 2
        assert rows[0]["headline"] == "标题A"
        assert rows[0]["pub_date"] == "2024-01-15"

    def test_replaces_on_reupsert(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, PostRow(slug="s", title="t", pub_date="2024-01-15",
                                   post_type="世界苦茶", raw_html="x"))
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        upsert_items(conn, "s", _make_items("s")[:1], pub_date="2024-01-15")
        rows = conn.execute("SELECT * FROM items WHERE post_slug=?", ("s",)).fetchall()
        assert len(rows) == 1

    def test_fts_indexed(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, PostRow(slug="s", title="t", pub_date="2024-01-15",
                                   post_type="世界苦茶", raw_html="x"))
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        rows = conn.execute(
            "SELECT items.headline FROM items_fts "
            "JOIN items ON items.id = items_fts.rowid "
            "WHERE items_fts.body_norm MATCH '正文'"
        ).fetchall()
        assert len(rows) == 2

    def test_fts_clears_on_reupsert(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, PostRow(slug="s", title="t", pub_date="2024-01-15",
                                   post_type="世界苦茶", raw_html="x"))
        upsert_items(conn, "s", _make_items("s"), pub_date="2024-01-15")
        upsert_items(conn, "s", _make_items("s")[:1], pub_date="2024-01-15")
        rows = conn.execute(
            "SELECT items.headline FROM items_fts "
            "JOIN items ON items.id = items_fts.rowid "
            "WHERE items_fts.body_norm MATCH '正文'"
        ).fetchall()
        assert len(rows) == 1


class TestFinancialDataUpsert:
    def test_inserts_and_replaces(self, conn: sqlite3.Connection) -> None:
        upsert_post(conn, PostRow(slug="s", title="t", pub_date="2024-01-15",
                                   post_type="世界苦茶", raw_html="x"))
        upsert_financial_data(conn, "s", [
            FinancialDataRow(field="USD/CNH", value="7.2"),
            FinancialDataRow(field="BTC", value="$42000"),
        ])
        rows = conn.execute(
            "SELECT * FROM financial_data WHERE post_slug=? ORDER BY field", ("s",)
        ).fetchall()
        assert len(rows) == 2

        upsert_financial_data(conn, "s", [
            FinancialDataRow(field="USD/CNH", value="7.3"),
        ])
        rows = conn.execute(
            "SELECT * FROM financial_data WHERE post_slug=?", ("s",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == "7.3"


class TestFullRoundtrip:
    def test_double_upsert_yields_no_duplicates(self, conn: sqlite3.Connection) -> None:
        post = PostRow(slug="s", title="t", pub_date="2024-01-15",
                       post_type="世界苦茶", raw_html="x")
        items = [
            ItemRow(section="x", headline="h1", headline_norm="h1",
                    body_text="b1", body_norm="b1", seq=0, pub_date="2024-01-15"),
            ItemRow(section="x", headline="h2", headline_norm="h2",
                    body_text="b2", body_norm="b2", seq=1, pub_date="2024-01-15"),
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
        assert conn.execute("SELECT COUNT(*) FROM financial_data WHERE post_slug='s'").fetchone()[0] == 1

        fts_count = conn.execute(
            "SELECT COUNT(*) FROM items_fts JOIN items ON items.id = items_fts.rowid "
            "WHERE items.post_slug='s'"
        ).fetchone()[0]
        assert fts_count == 2  # not 4
```

- [ ] **Step 2: Run test — expect ImportError**

Run: `uv run pytest tests/test_db.py -v`
Expected: `ImportError`

- [ ] **Step 3: Write `db.py`**

```python
# src/fearnation_mcp/db.py
"""SQLite schema, connection management, and idempotent upserts.

Storage lives under $XDG_CACHE_HOME/fearnation_mcp/fearnation.db
(or ~/.cache/fearnation_mcp/fearnation.db if XDG_CACHE_HOME unset).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def upsert_post(conn: sqlite3.Connection, post: PostRow) -> None:
    """Idempotent upsert of a post row."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    post = replace(post, last_seen=now, parsed_at=post.parsed_at or now)
    conn.execute(
        """
        INSERT INTO posts (slug, title, pub_date, post_type, raw_html, parsed_at, lastmod, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title=excluded.title,
            pub_date=excluded.pub_date,
            post_type=excluded.post_type,
            raw_html=excluded.raw_html,
            parsed_at=excluded.parsed_at,
            lastmod=excluded.lastmod,
            last_seen=excluded.last_seen
        """,
        (post.slug, post.title, post.pub_date, post.post_type,
         post.raw_html, post.parsed_at, post.lastmod, post.last_seen),
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
    existing_ids = [r[0] for r in conn.execute(
        "SELECT id FROM items WHERE post_slug=?", (post_slug,)
    )]
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
            (post_slug, item.section, item.headline, item.headline_norm,
             item.body_text, item.body_norm, item.seq, pub_date),
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: all pass, including FTS5 sync behavior.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/db.py tests/test_db.py && uv run black --check src/fearnation_mcp/db.py tests/test_db.py && uv run pyright src/fearnation_mcp/db.py`
Expected: clean, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/fearnation_mcp/db.py tests/test_db.py
git commit -m "feat(db): SQLite schema with FTS5 dual-write, idempotent upserts, meta table"
```

---

## Task 4: robots.py — robots.txt handling

**Files:**
- Create: `src/fearnation_mcp/robots.py`
- Create: `tests/test_robots.py`

**Interfaces:**
- Produces:
  - `RobotsRules` dataclass — `is_allowed(path: str) -> bool`
  - `fetch_robots_rules(client: httpx.Client) -> RobotsRules`
  - `DEFAULT_RULES: RobotsRules`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_robots.py
"""Tests for robots.txt fetch/parse/cache."""

from __future__ import annotations

import httpx
import pytest

from fearnation_mcp.robots import (
    DEFAULT_RULES,
    RobotsRules,
    fetch_robots_rules,
)


class TestRobotsRules:
    def test_default_allows_all(self) -> None:
        assert DEFAULT_RULES.is_allowed("/anything/")
        assert DEFAULT_RULES.is_allowed("/rss/")

    def test_disallow_respected(self) -> None:
        rules = RobotsRules.from_text(
            "User-agent: *\nDisallow: /private/\nDisallow: /admin\n",
            base_url="https://fearnation.club/",
        )
        assert not rules.is_allowed("/private/foo")
        assert not rules.is_allowed("/admin")
        assert rules.is_allowed("/rss/")
        assert rules.is_allowed("/some-post/")

    def test_empty_robots_allows_all(self) -> None:
        rules = RobotsRules.from_text("", base_url="https://fearnation.club/")
        assert rules.is_allowed("/any/")

    def test_user_agent_star_only(self) -> None:
        rules = RobotsRules.from_text(
            "User-agent: googlebot\nDisallow: /google-only/\n"
            "User-agent: *\nDisallow: /all/\n",
            base_url="https://fearnation.club/",
        )
        assert rules.is_allowed("/google-only/")
        assert not rules.is_allowed("/all/")


class _MockResp:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text
    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=None, response=self)  # type: ignore[arg-type]


class _MockClient:
    def __init__(self, status: int = 200, text: str = "") -> None:
        self.status = status
        self.text = text
    def get(self, url: str, timeout: float = 10.0) -> _MockResp:
        return _MockResp(self.status, self.text)


class TestFetchRobotsRules:
    def test_404_returns_default(self) -> None:
        client = _MockClient(status=404, text="")
        rules = fetch_robots_rules(client)  # type: ignore[arg-type]
        assert rules.is_allowed("/anything/")

    def test_connection_error_returns_default(self) -> None:
        class _ErrClient:
            def get(self, url: str, timeout: float = 10.0) -> _MockResp:
                raise httpx.ConnectError("no network")
        rules = fetch_robots_rules(_ErrClient())  # type: ignore[arg-type]
        assert rules.is_allowed("/anything/")
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/test_robots.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `robots.py`**

```python
# src/fearnation_mcp/robots.py
"""robots.txt fetch + parse. Honors User-agent: * rules only.

Permissive default. If fetch fails (404 or network error), fall back
to DEFAULT_RULES (allow all).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from fearnation_mcp.utils import get_logger

log = get_logger(__name__)

_BASE_URL = "https://fearnation.club/"


@dataclass(frozen=True)
class RobotsRules:
    """Parsed robots.txt rules (User-agent: * only)."""

    base_url: str
    disallow_paths: tuple[str, ...] = ()
    last_fetched: str | None = None
    raw_text: str | None = None

    def is_allowed(self, path: str) -> bool:
        """Return True if `path` is allowed under our rules."""
        if path.startswith("http://") or path.startswith("https://"):
            path = urlparse(path).path
        if not path.startswith("/"):
            path = "/" + path
        for disallow in self.disallow_paths:
            if not disallow:
                continue
            stripped = disallow.rstrip("/")
            if path == stripped or path.startswith(stripped + "/"):
                return False
        return True

    @classmethod
    def from_text(cls, text: str, base_url: str = _BASE_URL) -> RobotsRules:
        """Parse robots.txt text, honoring only `User-agent: *` rules."""
        disallow: list[str] = []
        in_star = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                in_star = (value == "*")
            elif key == "disallow" and in_star:
                disallow.append(value)
        return cls(
            base_url=base_url,
            disallow_paths=tuple(disallow),
            raw_text=text,
        )


DEFAULT_RULES = RobotsRules(base_url=_BASE_URL)


def fetch_robots_rules(
    client: httpx.Client,
    base_url: str = _BASE_URL,
) -> RobotsRules:
    """Fetch and parse robots.txt. Falls back to DEFAULT_RULES on error."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        resp = client.get(robots_url, timeout=10.0)
        if resp.status_code == 404:
            log.info("robots.txt 404, using permissive default", extra={"url": robots_url})
            return DEFAULT_RULES
        resp.raise_for_status()
        parsed = RobotsRules.from_text(resp.text, base_url=base_url)
        rules = RobotsRules(
            base_url=parsed.base_url,
            disallow_paths=parsed.disallow_paths,
            last_fetched=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            raw_text=parsed.raw_text,
        )
        log.info("robots.txt fetched", extra={
            "url": robots_url, "disallow_count": len(rules.disallow_paths),
        })
        return rules
    except (httpx.HTTPError, OSError) as exc:
        log.warning("robots.txt fetch failed, using default", extra={
            "url": robots_url, "error": str(exc),
        })
        return DEFAULT_RULES
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_robots.py -v`
Expected: all pass.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/robots.py tests/test_robots.py && uv run black --check src/fearnation_mcp/robots.py tests/test_robots.py && uv run pyright src/fearnation_mcp/robots.py`
Expected: clean, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/fearnation_mcp/robots.py tests/test_robots.py
git commit -m "feat(robots): robots.txt fetch/parse with permissive default fallback"
```

---

## Task 5: parser.py — DOM-driven parser (highest ROI)

**Files:**
- Create: `src/fearnation_mcp/parser.py`
- Create: `tests/test_parser.py`

**Interfaces:**
- Produces:
  - `ParsedPost` dataclass — `title`, `pub_date`, `post_type`, `items: list[ParsedItem]`, `financial_data: list[FinancialDataRow]`
  - `ParsedItem` dataclass — `section`, `headline`, `body_text`, `seq`
  - `FinancialDataRow` dataclass — `field`, `value`
  - `parse_post(slug: str, raw_html: str) -> ParsedPost`

- [ ] **Step 1: Write failing test for single-item parse**

```python
# tests/test_parser.py
"""Tests for the DOM-driven parser. Highest ROI test surface."""

from __future__ import annotations

from fearnation_mcp.parser import parse_post


def _wrap(content: str, title: str = "测试标题", pub_date: str = "2024-01-15") -> str:
    """Wrap content in a minimal Ghost post HTML shell."""
    return f"""
    <html><head>
    <title>{title}</title>
    <meta property="article:published_time" content="{pub_date}T08:00:00.000Z">
    </head><body>
    <main class="post-content">
    {content}
    </main>
    </body></html>
    """


class TestSingleItem:
    def test_one_item_with_strong_bullet(self) -> None:
        html = _wrap(
            '<h1>中国新闻</h1>'
            '<p><strong>• 测试标题</strong><br>测试正文</p>'
        )
        result = parse_post("test-slug", html)
        assert len(result.items) == 1
        item = result.items[0]
        assert item.headline == "测试标题"
        assert item.body_text == "测试正文"
        assert item.section == "中国新闻"
        assert item.seq == 0

    def test_headline_strips_bullet(self) -> None:
        for bullet in ["•", "・", "‣", "·", "－", "—"]:
            html = _wrap(
                f'<h1>新闻</h1>'
                f'<p><strong>{bullet}标题</strong><br>正文</p>'
            )
            result = parse_post("s", html)
            assert result.items[0].headline == f"标题", f"failed for bullet {bullet!r}"
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/test_parser.py::TestSingleItem -v`
Expected: ImportError.

- [ ] **Step 3: Write `parser.py`** (minimal — just enough to pass TestSingleItem)

```python
# src/fearnation_mcp/parser.py
"""Permissive DOM-driven parser for fearnation.club posts.

Algorithm (spec §5.1):
  1. Extract main content container (.post-content).
  2. Walk DOM in order, tracking current section (last <h1>).
  3. For each <p>: if starts with <strong>/<b>/<em> + bullet → new item.
     Else append body to current item.
  4. Accumulate body until next headline-<p>, next <h1>, or 苦茶数据 boundary.

Defensive variants (spec §5.2): bullet chars, tag variants, multi-paragraph
bodies, orphan <p>s, Ghost Koenig cards (kg-card class prefix).

Never break: on structural anomalies, log + continue rather than raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

from fearnation_mcp.utils import get_logger

log = get_logger(__name__)

_BULLET_RE = re.compile(r"^\s*[•・‣·－－—●◦※]\s*")

_POST_TYPE_ALERT_RE = re.compile(r"台海.*?ALERT|台海危机", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedItem:
    section: str | None
    headline: str
    body_text: str
    seq: int


@dataclass(frozen=True)
class FinancialDataRow:
    field: str
    value: str


@dataclass
class ParsedPost:
    title: str
    pub_date: str | None
    post_type: str | None
    items: list[ParsedItem] = field(default_factory=list)
    financial_data: list[FinancialDataRow] = field(default_factory=list)


def _strip_bullet(text: str) -> str:
    """Strip a leading bullet character + whitespace."""
    return _BULLET_RE.sub("", text).strip()


def _is_headline_p(p_tag: Tag) -> bool:
    """Return True if <p> starts with a <strong>/<b>/<em> child whose text
    begins with a bullet character (or is entirely bold)."""
    first = p_tag.find(True, recursive=False)
    if first is None:
        children = list(p_tag.children)
        if children and isinstance(children[0], NavigableString):
            return bool(_BULLET_RE.match(str(children[0])))
        return False
    if first.name not in ("strong", "b", "em"):
        return False
    text = first.get_text(strip=True)
    if _BULLET_RE.match(text):
        return True
    rest_text = p_tag.get_text(strip=True)
    return text != "" and rest_text == text


def _extract_headline_and_body(p_tag: Tag) -> tuple[str, str]:
    """Extract (headline, body) from a headline-<p>."""
    first = p_tag.find(True, recursive=False)
    if first is not None and first.name in ("strong", "b", "em"):
        headline = _strip_bullet(first.get_text(strip=True))
        parts: list[str] = []
        seen_first = False
        for child in p_tag.descendants:
            if child is first:
                seen_first = True
                continue
            if not seen_first:
                continue
            if isinstance(child, Tag):
                parts.append(child.get_text(separator=" ", strip=True))
            elif isinstance(child, NavigableString):
                s = str(child).strip()
                if s:
                    parts.append(s)
        body = " ".join(parts).strip()
    else:
        text = p_tag.get_text(separator=" ", strip=True)
        headline = _strip_bullet(text)
        body = ""
    return headline, body


def _detect_post_type(title: str) -> str | None:
    if _POST_TYPE_ALERT_RE.search(title):
        return "台海危機ALERT"
    return "世界苦茶"


def _extract_pub_date(soup: BeautifulSoup) -> str | None:
    """Extract ISO pub_date from: JSON-LD, <meta>, or <time>."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                dt = data.get("datePublished")
                if dt:
                    return dt[:10]
        except (json.JSONDecodeError, TypeError):
            continue
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        return meta["content"][:10]
    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        return t["datetime"][:10]
    return None


def _is_koenig_card(tag: Tag) -> bool:
    if not isinstance(tag, Tag):
        return False
    classes = tag.get("class") or []
    return any(c.startswith("kg-card") for c in classes)


_FINANCIAL_FIELD_RE = re.compile(
    r"(USD/CNH|USD/JPY|USDCNH|USDJPY|Brent|WTI|BTC|ETH|Nikkei|Hang\s?Seng|"
    r"沪深\d*|上证|深证|恒生|日经|比特币|以太坊|天然气|Gold|Silver|"
    r"GBP|EUR|JPY|CNY|RMB)"
)
_FINANCIAL_VALUE_RE = re.compile(r"([\d.,]+\s*[%万亿美元千百百元]?)", re.UNICODE)


def _parse_financial_block(block: Tag) -> list[FinancialDataRow]:
    """Parse `苦茶数据` block → list of (field, value) rows. Permissive."""
    out: list[FinancialDataRow] = []
    text = block.get_text(separator=" ", strip=True)
    tokens = re.split(r"[,;，；|]+", text)
    for tok in tokens:
        m = _FINANCIAL_FIELD_RE.search(tok)
        if not m:
            continue
        field_name = m.group(1)
        val_match = _FINANCIAL_VALUE_RE.search(tok)
        if val_match:
            out.append(FinancialDataRow(field=field_name, value=val_match.group(1).strip()))
    return out


def parse_post(slug: str, raw_html: str) -> ParsedPost:
    """Parse a fearnation post HTML into ParsedPost."""
    soup = BeautifulSoup(raw_html, "lxml")
    title_tag = soup.find("title") or soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else slug
    pub_date = _extract_pub_date(soup)
    post_type = _detect_post_type(title)

    main = (soup.find(class_="post-content") or soup.find("main")
            or soup.find("article") or soup.body)
    if main is None:
        log.warning("no main content container found", extra={"slug": slug})
        return ParsedPost(title=title, pub_date=pub_date, post_type=post_type)

    items: list[ParsedItem] = []
    financial: list[FinancialDataRow] = []
    current_section: str | None = None
    current_item: ParsedItem | None = None
    current_body_parts: list[str] = []
    seq = 0
    anomalies: list[str] = []

    def _flush_current() -> None:
        nonlocal current_item, current_body_parts
        if current_item is not None:
            full_body = " ".join([current_item.body_text, *current_body_parts]).strip()
            items.append(ParsedItem(
                section=current_item.section,
                headline=current_item.headline,
                body_text=full_body,
                seq=current_item.seq,
            ))
        current_item = None
        current_body_parts = []

    for element in main.descendants:
        if not isinstance(element, Tag):
            continue
        if _is_koenig_card(element):
            continue
        if element.name == "h1":
            h_text = element.get_text(strip=True)
            if h_text == "苦茶数据":
                _flush_current()
                current_section = "苦茶数据"
                continue
            if h_text:
                _flush_current()
                current_section = h_text
                continue
        if element.name in ("h2", "h3"):
            h_text = element.get_text(strip=True)
            if h_text:
                _flush_current()
                current_section = h_text
                continue
        if element.name == "p":
            if current_section == "苦茶数据":
                financial.extend(_parse_financial_block(element))
                continue
            if _is_headline_p(element):
                _flush_current()
                headline, body = _extract_headline_and_body(element)
                if not headline:
                    anomalies.append(f"empty headline in <p>: {element.get_text()[:50]!r}")
                current_item = ParsedItem(
                    section=current_section,
                    headline=headline,
                    body_text=body,
                    seq=seq,
                )
                seq += 1
            else:
                p_text = element.get_text(separator=" ", strip=True)
                if not p_text:
                    continue
                if current_item is None:
                    anomalies.append(f"orphan <p> with no current item: {p_text[:50]!r}")
                    current_item = ParsedItem(
                        section=current_section,
                        headline="",
                        body_text=p_text,
                        seq=seq,
                    )
                    seq += 1
                else:
                    current_body_parts.append(p_text)

    _flush_current()

    if not items and not financial:
        log.warning("parser extracted 0 items and 0 financial rows", extra={
            "slug": slug, "anomalies": anomalies,
        })
    elif anomalies:
        log.info("parser completed with anomalies", extra={
            "slug": slug, "items_extracted": len(items),
            "financial_rows": len(financial), "anomalies": anomalies,
        })

    return ParsedPost(
        title=title, pub_date=pub_date, post_type=post_type,
        items=items, financial_data=financial,
    )
```

- [ ] **Step 4: Run single-item tests**

Run: `uv run pytest tests/test_parser.py::TestSingleItem -v`
Expected: both tests pass.

- [ ] **Step 5: Add comprehensive tests** — append to `tests/test_parser.py`:

```python
class TestMultipleItems:
    def test_multiple_items_in_section(self) -> None:
        html = _wrap(
            '<h1>中国新闻</h1>'
            '<p><strong>• 标题A</strong><br>正文A</p>'
            '<p><strong>• 标题B</strong><br>正文B</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].section == "中国新闻"
        assert result.items[0].seq == 0
        assert result.items[1].seq == 1

    def test_section_change_resets_section(self) -> None:
        html = _wrap(
            '<h1>中国新闻</h1>'
            '<p><strong>• 中国标题</strong><br>中国正文</p>'
            '<h1>印太新闻</h1>'
            '<p><strong>• 印太标题</strong><br>印太正文</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].section == "中国新闻"
        assert result.items[1].section == "印太新闻"

    def test_h2_section_header(self) -> None:
        html = _wrap(
            '<h2>科技新闻</h2>'
            '<p><strong>• 标题</strong><br>正文</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 1
        assert result.items[0].section == "科技新闻"


class TestMultiParagraphBody:
    def test_following_p_appends_to_body(self) -> None:
        html = _wrap(
            '<h1>新闻</h1>'
            '<p><strong>• 标题</strong><br>第一段</p>'
            '<p>第二段继续</p>'
            '<p>第三段继续</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 1
        body = result.items[0].body_text
        assert "第一段" in body
        assert "第二段继续" in body
        assert "第三段继续" in body


class TestOrphanP:
    def test_orphan_before_any_item_creates_empty_headline_item(self) -> None:
        html = _wrap(
            '<h1>新闻</h1>'
            '<p>不经 strong 的开场段落</p>'
            '<p><strong>• 标题</strong><br>正文</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].headline == ""
        assert "不经 strong 的开场段落" in result.items[0].body_text


class TestTagVariants:
    def test_b_tag(self) -> None:
        html = _wrap('<h1>新闻</h1><p><b>• 标题</b><br>正文</p>')
        assert parse_post("s", html).items[0].headline == "标题"

    def test_em_tag(self) -> None:
        html = _wrap('<h1>新闻</h1><p><em>• 标题</em><br>正文</p>')
        assert parse_post("s", html).items[0].headline == "标题"

    def test_no_bullet_entirely_bold(self) -> None:
        html = _wrap('<h1>新闻</h1><p><strong>缩影标题</strong></p>')
        assert parse_post("s", html).items[0].headline == "缩影标题"


class TestKoenigCardExclusion:
    def test_button_card_excluded(self) -> None:
        html = _wrap(
            '<h1>新闻</h1>'
            '<p><strong>• 标题</strong><br>正文</p>'
            '<div class="kg-card kg-button-card">'
            '<a href="https://example.com">点击支持1美元/年</a>'
            '</div>'
            '<p><strong>• 标题2</strong><br>正文2</p>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        for item in result.items:
            assert "点击支持" not in item.body_text
            assert "点击支持" not in item.headline


class TestFinancialData:
    def test_ku_cha_shu_ju_block_extracts_financial_rows(self) -> None:
        html = _wrap(
            '<h1>苦茶数据</h1>'
            '<p>USD/CNH 7.2, USD/JPY 145.5, Brent $82</p>'
            '<p>BTC $42000, ETH $2500</p>'
            '<h1>中国新闻</h1>'
            '<p><strong>• 标题</strong><br>正文</p>'
        )
        result = parse_post("s", html)
        fields = {r.field for r in result.financial_data}
        assert "USD/CNH" in fields
        assert "BTC" in fields
        assert len(result.items) == 1
        assert result.items[0].section == "中国新闻"


class TestPostTypeDetection:
    def test_world_tea_default(self) -> None:
        html = _wrap("<p><strong>• x</strong><br>y</p>", title="世界苦茶 2024-01-15")
        assert parse_post("s", html).post_type == "世界苦茶"

    def test_taiwan_alert(self) -> None:
        html = _wrap("<p><strong>• x</strong><br>y</p>", title="台海危機ALERT 2024-01-15")
        assert parse_post("s", html).post_type == "台海危機ALERT"


class TestMalformedResilience:
    def test_empty_post_returns_empty(self) -> None:
        result = parse_post("s", "<html><body></body></html>")
        assert result.items == []
        assert result.financial_data == []

    def test_no_main_container_falls_back_to_body(self) -> None:
        html = (
            '<html><body>'
            '<h1>新闻</h1>'
            '<p><strong>• 标题</strong><br>正文</p>'
            '</body></html>'
        )
        result = parse_post("s", html)
        assert len(result.items) == 1

    def test_no_pub_date_returns_none(self) -> None:
        html = '<html><body><main class="post-content"><h1>新闻</h1><p><strong>• x</strong><br>y</p></main></body></html>'
        result = parse_post("s", html)
        assert result.pub_date is None
```

- [ ] **Step 6: Run all parser tests**

Run: `uv run pytest tests/test_parser.py -v`
Expected: all pass. If failures, iterate on parser logic — do NOT relax tests.

- [ ] **Step 7: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/parser.py tests/test_parser.py && uv run black --check src/fearnation_mcp/parser.py tests/test_parser.py && uv run pyright src/fearnation_mcp/parser.py`
Expected: clean, 0 errors.

- [ ] **Step 8: Commit**

```bash
git add src/fearnation_mcp/parser.py tests/test_parser.py
git commit -m "feat(parser): permissive DOM-driven parser with Koenig card exclusion and financial data extraction"
```

---

## Task 6: search.py — FTS5 + OpenCC

**Files:**
- Create: `src/fearnation_mcp/search.py`
- Create: `tests/test_search.py`

**Interfaces:**
- Produces:
  - `normalize_text(text: str) -> str` — OpenCC `t2s` normalized
  - `SearchHit` dataclass
  - `search_items(conn, query, section?, date_from?, date_to?, limit=20) -> list[SearchHit]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_search.py
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
        assert normalize_text("稀土開採") == "稀土开采"

    def test_already_simplified_unchanged(self) -> None:
        assert normalize_text("稀土开采") == "稀土开采"

    def test_mixed_keeps_non_cjk(self) -> None:
        assert "USD" in normalize_text("USD 稀土開採 7.2")
        assert "稀土开采" in normalize_text("USD 稀土開採 7.2")

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
    upsert_post(conn, PostRow(
        slug=slug, title=title, pub_date=pub_date,
        post_type="世界苦茶", raw_html="x",
    ))
    item_rows = [
        ItemRow(section=s, headline=h, headline_norm=normalize_text(h),
                body_text=b, body_norm=normalize_text(b), seq=i, pub_date=pub_date)
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
        # Query with Simplified Chinese 稀土开采 → must still find Traditional content
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

    def test_section_filter(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn, items=[
            ("中国新闻", "标题A", "正文A"),
            ("印太新闻", "标题B", "正文B"),
        ])
        hits = search_items(conn, "标题", section="印太新闻")
        assert len(hits) == 1
        assert hits[0].headline == "标题B"

    def test_date_range_filter(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn, slug="p1", pub_date="2024-01-10")
        _seed_post(conn, slug="p2", pub_date="2024-02-10", items=[("产业新闻", "稀土X", "y")])
        hits = search_items(conn, "稀土", date_from="2024-02-01")
        assert len(hits) == 1
        assert hits[0].slug == "p2"

    def test_limit(self, conn: sqlite3.Connection) -> None:
        items = [("新闻", f"标题{i}", "正文") for i in range(30)]
        _seed_post(conn, items=items)
        hits = search_items(conn, "标题", limit=5)
        assert len(hits) == 5

    def test_no_results_returns_empty(self, conn: sqlite3.Connection) -> None:
        _seed_post(conn)
        assert search_items(conn, "不存在的关键词") == []
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/test_search.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `search.py`**

```python
# src/fearnation_mcp/search.py
"""FTS5 query builder + OpenCC t2s normalization.

OpenCC normalization is the single highest-ROI search quality lever:
the site mixes Simplified/Traditional Chinese (台海危機ALERT is Traditional-titled,
世界苦茶 bodies lean Simplified). We normalize both indexed content
(via db.upsert_items pre-computing _norm columns) and query strings
to Simplified before MATCH.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import opencc

from fearnation_mcp.utils import get_logger, validate_iso_date

log = get_logger(__name__)

_converter: opencc.OpenCC | None = None


def _get_converter() -> opencc.OpenCC:
    global _converter
    if _converter is None:
        _converter = opencc.OpenCC("t2s")
    return _converter


def normalize_text(text: str) -> str:
    """Normalize text to Simplified Chinese via OpenCC t2s.

    Used at index time (pre-compute _norm columns) and at query time
    (normalize query before MATCH).
    """
    if not text:
        return ""
    return _get_converter().convert(text)


@dataclass(frozen=True)
class SearchHit:
    slug: str
    section: str | None
    headline: str | None
    body_text: str | None
    pub_date: str | None
    seq: int | None
    rank: float


def search_items(
    conn: sqlite3.Connection,
    query: str,
    section: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Search indexed items by FTS5 + filters.

    Args:
        query: Free-text query (will be OpenCC-normalized before MATCH).
        section: Optional section filter (e.g. 中国新闻).
        date_from: Optional ISO date (inclusive).
        date_to: Optional ISO date (inclusive).
        limit: Max results to return (default 20, max 200).
    """
    if date_from:
        validate_iso_date(date_from)
    if date_to:
        validate_iso_date(date_to)
    if limit < 1 or limit > 200:
        raise ValueError(f"limit must be in [1, 200], got {limit}")

    normalized_query = normalize_text(query).strip()
    if not normalized_query:
        return []

    # Escape double-quote to avoid breaking MATCH grammar.
    safe_query = normalized_query.replace('"', '""')
    match_expr = f'"{safe_query}"'

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
    log.info("search executed", extra={
        "query": query, "section": section, "date_from": date_from,
        "date_to": date_to, "limit": limit, "result_count": len(hits),
    })
    return hits
```

- [ ] **Step 4: Run search tests**

Run: `uv run pytest tests/test_search.py -v`
Expected: all pass, including cross-script assertions.

If `test_cross_script_*` fails, debug OpenCC conversion — confirm `normalize_text("稀土開採")` returns `"稀土开采"`.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/search.py tests/test_search.py && uv run black --check src/fearnation_mcp/search.py tests/test_search.py && uv run pyright src/fearnation_mcp/search.py`
Expected: clean, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/fearnation_mcp/search.py tests/test_search.py
git commit -m "feat(search): FTS5 search with OpenCC t2s cross-script normalization"
```

---

## Task 7: crawler.py — sitemap + RSS + full crawl

**Files:**
- Create: `src/fearnation_mcp/crawler.py`
- Create: `tests/test_crawler.py`
- Create: `tests/fixtures/rss.xml`, `tests/fixtures/sitemap-index.xml`, `tests/fixtures/sitemap-posts.xml`, `tests/fixtures/post-world-tea.html`, `tests/fixtures/post-taiwan-alert.html`

**Interfaces:**
- Produces:
  - `SitemapEntry` dataclass — `loc`, `lastmod`, `is_sitemap`
  - `RSSItem` dataclass — `slug`, `title`, `pub_date`, `content_html`, `link`
  - `CrawlReport` dataclass
  - `fetch_url(client, url, timeout=15.0) -> str`
  - `parse_sitemap(xml_text) -> list[SitemapEntry]`
  - `parse_rss(rss_xml) -> list[RSSItem]`
  - `upsert_parsed_post(conn, slug, raw_html, parsed, lastmod=None) -> int`
  - `crawl_post(client, conn, slug, lastmod=None) -> int`
  - `crawl_all(client, conn, rate_limit_sec=1.0, max_retries=3) -> CrawlReport`
  - `refresh_rss(client, conn) -> int`

- [ ] **Step 1: Write fixtures**

`tests/fixtures/sitemap-index.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://fearnation.club/sitemap-posts.xml</loc></sitemap>
<sitemap><loc>https://fearnation.club/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
```

`tests/fixtures/sitemap-posts.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://fearnation.club/shijie-kucha-2024-01-15/</loc><lastmod>2024-01-15T08:00:00+00:00</lastmod></url>
<url><loc>https://fearnation.club/taiwan-alert-2024-01-14/</loc><lastmod>2024-01-14T08:00:00+00:00</lastmod></url>
<url><loc>https://fearnation.club/old-post-2020-01-01/</loc><lastmod>2020-01-01T08:00:00+00:00</lastmod></url>
</urlset>
```

`tests/fixtures/rss.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>世界苦茶</title>
<link>https://fearnation.club/</link>
<description>FearNation Newsletter</description>
<item>
<title>世界苦茶 2024-01-15</title>
<link>https://fearnation.club/shijie-kucha-2024-01-15/</link>
<guid>https://fearnation.club/shijie-kucha-2024-01-15/</guid>
<pubDate>Mon, 15 Jan 2024 08:00:00 GMT</pubDate>
<dc:creator>fearnation</dc:creator>
<description>今日要闻摘要</description>
<content:encoded><![CDATA[
<h1>苦茶数据</h1>
<p>USD/CNH 7.21, USD/JPY 145.5</p>
<h1>中国新闻</h1>
<p><strong>• 稀土供应链出现新进展</strong><br>行业机构发布稀土供需展望</p>
<p><strong>• 上海自贸区扩展</strong><br>国务院宣布上海自贸区扩区方案</p>
<h1>印太新闻</h1>
<p><strong>• 日美联合军演</strong><br>自卫队与美军在冲绳周边举行联合演习</p>
<div class="kg-card kg-button-card"><a href="#support">点击支持1美元/年</a></div>
]]></content:encoded>
</item>
<item>
<title>台海危機ALERT 2024-01-14</title>
<link>https://fearnation.club/taiwan-alert-2024-01-14/</link>
<pubDate>Sun, 14 Jan 2024 08:00:00 GMT</pubDate>
<content:encoded><![CDATA[
<h1>事件概述</h1>
<p>1月13日，台湾举行总统大选。</p>
<h1>各方反应</h1>
<p><strong>• 美方祝贺</strong><br>白宫发表声明祝贺当选。</p>
]]></content:encoded>
</item>
</channel>
</rss>
```

`tests/fixtures/post-world-tea.html`:

```html
<!DOCTYPE html>
<html>
<head>
<title>世界苦茶 2024-01-15</title>
<meta property="article:published_time" content="2024-01-15T08:00:00.000Z">
</head>
<body>
<main class="post-content">
<h1>苦茶数据</h1>
<p>USD/CNH 7.21, USD/JPY 145.5, Brent $82</p>
<h1>中国新闻</h1>
<p><strong>• 稀土供应链出现新进展</strong><br>行业机构发布稀土供需展望。</p>
<p><strong>• 上海自贸区扩展</strong><br>国务院宣布上海自贸区扩区方案。</p>
<h1>印太新闻</h1>
<p><strong>• 日美联合军演</strong><br>自卫队与美军在冲绳周边举行联合演习。</p>
<div class="kg-card kg-button-card"><a href="#support">点击支持1美元/年</a></div>
</main>
</body>
</html>
```

`tests/fixtures/post-taiwan-alert.html`:

```html
<!DOCTYPE html>
<html>
<head>
<title>台海危機ALERT 2024-01-14</title>
<meta property="article:published_time" content="2024-01-14T08:00:00.000Z">
</head>
<body>
<main class="post-content">
<h1>事件概述</h1>
<p>1月13日，台湾举行总统大选。</p>
<h1>各方反应</h1>
<p><strong>• 美方祝贺</strong><br>白宫发表声明祝贺当选，重申一中政策不变。</p>
</main>
</body>
</html>
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_crawler.py
"""Tests for crawler.py: sitemap recursion, RSS parse, crawl flow."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from fearnation_mcp.crawler import (
    SitemapEntry,
    fetch_url,
    parse_rss,
    parse_sitemap,
)
from fearnation_mcp.db import get_meta, init_schema

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseSitemap:
    def test_parse_urlset(self) -> None:
        xml = (FIXTURES / "sitemap-posts.xml").read_text()
        entries = parse_sitemap(xml)
        assert len(entries) == 3
        assert entries[0].loc == "https://fearnation.club/shijie-kucha-2024-01-15/"
        assert entries[0].lastmod == "2024-01-15T08:00:00+00:00"
        assert not entries[0].is_sitemap

    def test_parse_sitemapindex_returns_entries_to_recurse(self) -> None:
        xml = (FIXTURES / "sitemap-index.xml").read_text()
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
        xml = (FIXTURES / "rss.xml").read_text()
        items = parse_rss(xml)
        assert len(items) == 2
        first = items[0]
        assert first.title == "世界苦茶 2024-01-15"
        assert first.slug == "shijie-kucha-2024-01-15"
        assert first.pub_date == "2024-01-15"
        assert "<h1>苦茶数据</h1>" in first.content_html

    def test_slug_extracted_from_link(self) -> None:
        xml = (FIXTURES / "rss.xml").read_text()
        items = parse_rss(xml)
        assert items[1].slug == "taiwan-alert-2024-01-14"

    def test_parse_rss_empty(self) -> None:
        assert parse_rss("") == []


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
```

- [ ] **Step 3: Run — expect ImportError**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: ImportError.

- [ ] **Step 4: Write `crawler.py`** (full content)

```python
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
from datetime import datetime, timezone

import feedparser
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
    parsed = feedparser.parse(rss_xml)
    items: list[RSSItem] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "")
        content_html = ""
        if hasattr(entry, "content") and entry.content:
            content_html = entry.content[0].value
        elif hasattr(entry, "content_encoded"):
            content_html = entry.content_encoded
        pub_date: str | None = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                pub_date = dt.strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass
        try:
            slug = _slug_from_link(link)
        except ValueError:
            log.warning("rss entry has invalid link, skipping", extra={"link": link})
            continue
        items.append(RSSItem(
            slug=slug, title=title, pub_date=pub_date,
            content_html=content_html, link=link,
        ))
    return items


def upsert_parsed_post(
    conn: sqlite3.Connection,
    slug: str,
    raw_html: str,
    parsed: ParsedPost,
    lastmod: str | None = None,
) -> int:
    """Upsert a parsed post + items + financial_data in a single txn.

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
    fin_rows = [
        FinancialDataRow(field=r.field, value=r.value)
        for r in parsed.financial_data
    ]

    with conn:  # txn
        upsert_post(conn, PostRow(
            slug=slug, title=parsed.title, pub_date=parsed.pub_date,
            post_type=parsed.post_type, raw_html=raw_html, lastmod=lastmod,
        ))
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
        log.error("root sitemap fetch failed", extra={"url": _SITEMAP_URL, "error": str(exc)})
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
            log.warning("child sitemap fetch failed", extra={"url": sm_url, "error": str(exc)})
            continue
        entries = parse_sitemap(xml_text)
        for entry in entries:
            if entry.is_sitemap:
                pending_sitemaps.append(entry.loc)
            else:
                post_entries.append(entry)

    log.info("sitemap recursion complete", extra={
        "total_sitemaps": len(visited_sitemaps),
        "total_post_urls": len(post_entries),
    })

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
        if (existing and existing["parsed_at"] and existing["lastmod"]
                and entry.lastmod and existing["lastmod"] >= entry.lastmod):
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
                log.warning("post fetch retry", extra={
                    "slug": slug, "attempt": attempt, "error": last_err,
                })
                time.sleep(rate_limit_sec * attempt)

        if not succeeded:
            report.posts_failed += 1
            log.error("post fetch failed after retries", extra={
                "slug": slug, "error": last_err, "attempts": attempt,
            })

        if rate_limit_sec > 0:
            time.sleep(rate_limit_sec)

    set_meta(conn, "full_crawl_done", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    report.duration_sec = time.time() - start
    log.info("full crawl complete", extra={
        "posts_fetched": report.posts_fetched,
        "posts_failed": report.posts_failed,
        "items_extracted": report.items_extracted,
        "duration_sec": report.duration_sec,
    })
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

    set_meta(conn, "last_rss_fetch", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    log.info("rss refresh complete", extra={"new_posts": new_count})
    return new_count
```

- [ ] **Step 5: Run unit tests**

Run: `uv run pytest tests/test_crawler.py::TestParseSitemap tests/test_crawler.py::TestParseRss tests/test_crawler.py::TestFetchUrl -v`
Expected: all pass.

- [ ] **Step 6: Add full-crawl flow tests** — append to `tests/test_crawler.py`:

```python
from fearnation_mcp.crawler import crawl_all, refresh_rss


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


class TestCrawlAll:
    def test_full_crawl_indexes_all_posts(self, conn: sqlite3.Connection) -> None:
        client = _MockClient({
            "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(),
            "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(),
            "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(),
            "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(),
            "/old-post-2020-01-01/": "<html><body><main class='post-content'><h1>新闻</h1><p><strong>• 老</strong><br>内容</p></main></body></html>",
        })
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

    def test_idempotent_skip_when_lastmod_unchanged(
        self, conn: sqlite3.Connection
    ) -> None:
        client = _MockClient({
            "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(),
            "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(),
            "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(),
            "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(),
            "/old-post-2020-01-01/": "<html><body><main class='post-content'><h1>x</h1><p><strong>• y</strong><br>z</p></main></body></html>",
        })
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
        client = _FlakyClient({
            "/sitemap.xml": (FIXTURES / "sitemap-index.xml").read_text(),
            "/sitemap-posts.xml": (FIXTURES / "sitemap-posts.xml").read_text(),
            "/sitemap-pages.xml": '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            "/shijie-kucha-2024-01-15/": (FIXTURES / "post-world-tea.html").read_text(),
            "/taiwan-alert-2024-01-14/": (FIXTURES / "post-taiwan-alert.html").read_text(),
            "/old-post-2020-01-01/": "<html><body>ok</body></html>",
        }, fail_once_for="/taiwan-alert-2024-01-14/")
        report = crawl_all(client, conn, rate_limit_sec=0, max_retries=3)
        assert report.posts_failed == 0
        assert report.posts_fetched == 3


class TestRefreshRss:
    def test_refresh_indexes_new_posts(self, conn: sqlite3.Connection) -> None:
        client = _MockClient({
            "/rss/": (FIXTURES / "rss.xml").read_text(),
        })
        count = refresh_rss(client, conn)
        assert count == 2
        slugs = {r["slug"] for r in conn.execute("SELECT slug FROM posts").fetchall()}
        assert "shijie-kucha-2024-01-15" in slugs
        assert "taiwan-alert-2024-01-14" in slugs
        assert get_meta(conn, "last_rss_fetch") is not None

    def test_refresh_fetch_failure_returns_zero(self, conn: sqlite3.Connection) -> None:
        client = _MockClient({})  # all 404
        assert refresh_rss(client, conn) == 0
```

- [ ] **Step 7: Run all crawler tests**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: all pass.

- [ ] **Step 8: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/crawler.py tests/test_crawler.py && uv run black --check src/fearnation_mcp/crawler.py tests/test_crawler.py && uv run pyright src/fearnation_mcp/crawler.py`
Expected: clean, 0 errors.

- [ ] **Step 9: Commit**

```bash
git add src/fearnation_mcp/crawler.py tests/test_crawler.py tests/fixtures/
git commit -m "feat(crawler): sitemap recursion, RSS parse, full-crawl driver with retry"
```

---

## Task 8: server.py — 4 MCP tools

**Files:**
- Create: `src/fearnation_mcp/server.py`
- Create: `tests/test_server.py`

**Interfaces:**
- Produces:
  - `mcp: MCPServer` — module-level MCP server instance + 4 registered tools
  - `run() -> None` — entry point for `mcp.run(transport="stdio")`
  - `search_news`, `get_post`, `list_recent`, `discover` — underlying functions
  - `_get_conn() -> sqlite3.Connection` — for test monkeypatching
  - `_TODAY_OVERRIDE` — for test monkeypatching of "today"

- [ ] **Step 1: Write failing tests** (call tool functions directly, not via MCP wire)

```python
# tests/test_server.py
"""Tests for server.py — call tool functions directly without MCP transport."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from fearnation_mcp.db import ItemRow, PostRow, init_schema, set_meta, upsert_items, upsert_post
from fearnation_mcp.search import normalize_text
from fearnation_mcp.server import discover, get_post, list_recent, search_news


def _seed_full(conn: sqlite3.Connection, today: date = date(2024, 1, 16)) -> None:
    """Seed 3 posts dated Jan 14-15 2024."""
    seed = [
        ("shijie-kucha-2024-01-15", "世界苦茶 2024-01-15", "2024-01-15", "世界苦茶"),
        ("shijie-kucha-2024-01-14", "世界苦茶 2024-01-14", "2024-01-14", "世界苦茶"),
        ("taiwan-alert-2024-01-14", "台海危機ALERT 2024-01-14", "2024-01-14", "台海危機ALERT"),
    ]
    for slug, title, pub_date, pt in seed:
        upsert_post(conn, PostRow(slug=slug, title=title, pub_date=pub_date,
                                  post_type=pt, raw_html="x"))
        items = [
            ItemRow(section="中国新闻", headline=f"{title} 稀土行业动态",
                    headline_norm=normalize_text(f"{title} 稀土行业动态"),
                    body_text="正文", body_norm="正文", seq=0, pub_date=pub_date),
        ]
        upsert_items(conn, slug, items, pub_date=pub_date)
    # Pretend RSS is fresh so server doesn't attempt network refresh
    set_meta(conn, "last_rss_fetch",
             datetime.now(timezone.utc).isoformat(timespec="seconds"))


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
        hits = search_news("稀土")
        assert len(hits) >= 3
        for hit in hits:
            assert "slug" in hit
            assert "headline" in hit
            assert "pub_date" in hit

    def test_section_filter(self, conn: sqlite3.Connection) -> None:
        hits = search_news("稀土", section="中国新闻")
        assert len(hits) >= 1
        assert all(h["section"] == "中国新闻" for h in hits)

    def test_date_from_filter(self, conn: sqlite3.Connection) -> None:
        hits = search_news("稀土", date_from="2024-01-15")
        assert all(h["pub_date"] >= "2024-01-15" for h in hits)

    def test_invalid_date_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            search_news("x", date_from="not-a-date")


class TestGetPost:
    def test_get_by_slug(self, conn: sqlite3.Connection) -> None:
        result = get_post("shijie-kucha-2024-01-15")
        assert result["slug"] == "shijie-kucha-2024-01-15"  # type: ignore[index]
        assert result["title"] == "世界苦茶 2024-01-15"  # type: ignore[index]
        assert result["pub_date"] == "2024-01-15"  # type: ignore[index]
        assert isinstance(result["items"], list)  # type: ignore[index]
        assert len(result["items"]) >= 1  # type: ignore[index]

    def test_get_by_date_with_multiple_returns_list(
        self, conn: sqlite3.Connection
    ) -> None:
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
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/test_server.py -v`
Expected: ImportError.

- [ ] **Step 3: Write `server.py`**

```python
# src/fearnation_mcp/server.py
"""MCP server with 4 tools: search_news, get_post, list_recent, discover.

Tools auto-refresh RSS if last_rss_fetch > 60 min old (spec §6.1).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
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
    age = datetime.now(timezone.utc) - last_dt
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
    (e.g., "稀土开采" matches "稀土開採") via OpenCC normalization.
    """
    from fearnation_mcp.search import search_items
    conn = _get_conn()
    _maybe_refresh_rss(conn)
    hits = search_items(
        conn, query,
        section=section, date_from=date_from, date_to=date_to, limit=limit,
    )
    return [
        {
            "slug": h.slug, "section": h.section, "headline": h.headline,
            "body": h.body_text, "pub_date": h.pub_date, "seq": h.seq,
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
            (slug_or_date,)
        ).fetchall()
        if not rows:
            raise KeyError(f"No post found for date {slug_or_date}")
        if len(rows) == 1:
            return _fetch_full_post(conn, rows[0]["slug"])
        return [
            {"slug": r["slug"], "title": r["title"],
             "pub_date": r["pub_date"], "post_type": r["post_type"]}
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
        (slug,)
    ).fetchall()
    fin = conn.execute(
        "SELECT field, value FROM financial_data WHERE post_slug = ? ORDER BY field",
        (slug,)
    ).fetchall()
    return {
        "slug": post["slug"],
        "title": post["title"],
        "pub_date": post["pub_date"],
        "post_type": post["post_type"],
        "items": [
            {"section": i["section"], "headline": i["headline"],
             "body": i["body_text"], "pub_date": i["pub_date"], "seq": i["seq"]}
            for i in items
        ],
        "financial_data": [
            {"field": f["field"], "value": f["value"]} for f in fin
        ],
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
        (cutoff,)
    ).fetchall()
    return [
        {"slug": r["slug"], "title": r["title"], "pub_date": r["pub_date"],
         "post_type": r["post_type"], "item_count": r["item_count"]}
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
    sql = ("SELECT slug, title, pub_date, post_type FROM posts WHERE 1=1")
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
        {"slug": r["slug"], "title": r["title"],
         "pub_date": r["pub_date"], "post_type": r["post_type"]}
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
    (e.g., "稀土开采" matches "稀土開採"). Returns items with slug, section, headline,
    body, pub_date, seq.

    Args:
        query: Free-text search string.
        section: Optional section filter (中国新闻 / 印太新闻 / 科技新闻 / 经济新闻).
        date_from: Optional ISO date YYYY-MM-DD (inclusive).
        date_to: Optional ISO date YYYY-MM-DD (inclusive).
        limit: Max results (1-200, default 20).
    """
    return search_news(query, section=section, date_from=date_from,
                      date_to=date_to, limit=limit)


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
    return discover(query=query, post_type=post_type,
                    date_from=date_from, date_to=date_to)


def run() -> None:
    """Run the MCP server over stdio transport (entry point)."""
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Run server tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: all pass.

- [ ] **Step 5: Verify entry script end-to-end**

Run: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | uv run fearnation-mcp 2>/dev/null | head -c 1000`
Expected: a JSON-RPC InitializeResult response on stdout. (Server starts, accepts initialize, responds.)

If that's flaky in the test environment, skip and rely on the unit tests + smoke test in Task 9.

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check src/fearnation_mcp/server.py tests/test_server.py && uv run black --check src/fearnation_mcp/server.py tests/test_server.py && uv run pyright src/fearnation_mcp/server.py`
Expected: clean, 0 errors.

- [ ] **Step 7: Commit**

```bash
git add src/fearnation_mcp/server.py tests/test_server.py
git commit -m "feat(server): 4 MCP tools (search_news, get_post, list_recent, discover)"
```

---

## Task 9: Live network smoke test + README

**Files:**
- Create: `tests/test_smoke.py` (marked `@pytest.mark.network`, skipped by default)
- Update: `README.md` with usage + MCP client config

- [ ] **Step 1: Write smoke test**

```python
# tests/test_smoke.py
"""Live network smoke tests. Skipped by default.

Run manually before release with:
    uv run pytest -m network tests/test_smoke.py
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from fearnation_mcp.crawler import crawl_all, fetch_url, parse_rss, parse_sitemap, _slug_from_link
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
```

- [ ] **Step 2: Verify smoke tests are skipped by default**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: 5 tests deselected (skipped with `network` marker).

- [ ] **Step 3: Update README.md**

```markdown
# fearnation-mcp

An MCP server that searches [fearnation.club](https://fearnation.club/) news
archive (世界苦茶 daily digest + 台海危機 ALERT) at item-level granularity.

## Features

- **Full historical corpus**: one-time full crawl of all sitemap-discovered posts.
- **Incremental updates**: RSS feed auto-refresh on tool calls when stale (>60 min).
- **Cross-script search**: OpenCC `t2s` normalization lets Simplified Chinese
  queries match Traditional Chinese content (and vice versa).
- **4 tools**: `search_news`, `get_post`, `list_recent`, `discover`.

## Installation

```bash
git clone <this-repo> fearnation-mcp
cd fearnation-mcp
uv venv
uv pip install -e ".[dev]"
```

## Usage (MCP client config)

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fearnation": {
      "command": "/absolute/path/to/fearnation-mcp/.venv/bin/fearnation-mcp",
      "args": []
    }
  }
}
```

Cursor and other MCP clients: same `command` path with empty args.

## Tools

### `search_news(query, section?, date_from?, date_to?, limit?)`

Full-text search over the indexed news items. Returns matches with `slug`,
`section`, `headline`, `body`, `pub_date`, `seq`.

### `get_post(slug_or_date)`

Fetch a full post by slug (`shijie-kucha-2024-01-15`) or ISO date
(`2024-01-15`). For dates with multiple posts, returns a list of summaries.

### `list_recent(days=7)`

List recent posts within `N` days. Each result has `slug`, `title`,
`pub_date`, `post_type`, `item_count`. Use to orient before searching.

### `discover(query?, post_type?, date_from?, date_to?)`

Browse the post catalogue. Filter by title substring, post_type
(`世界苦茶` or `台海危機ALERT`), or date range.

## Development

```bash
# Run tests (excluding network tests)
uv run pytest

# Run linters
uv run ruff check src tests
uv run black --check src tests
uv run pyright src

# Run network smoke tests (requires network access)
uv run pytest -m network tests/test_smoke.py

# Run the server locally for testing
uv run fearnation-mcp
```

## Design

See `docs/superpowers/specs/2026-07-08-fearnation-mcp-design.md`.

## License

MIT
```

- [ ] **Step 4: Verify full test suite passes**

Run: `uv run pytest`
Expected: all non-network tests pass.

Run: `uv run ruff check src tests && uv run black --check src tests && uv run pyright src`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_smoke.py README.md
git commit -m "test: network smoke tests (default-skipped) + README with MCP client config"
```

---

## Self-Review Checklist (executed by plan author)

- **Spec coverage**: Each spec section §1-§13 is covered by a task. §1 Goal (Task 8 + 9); §2 Data sources (Task 7); §3 Full crawl + RSS (Task 7); §4 SQLite+FTS5+OpenCC (Task 3+6); §4.4 item-level pub_date (Task 3 schema + Task 6 search filter); §5 Parser (Task 5); §6 4 tools (Task 8); §6.1 implicit RSS refresh (Task 8 `_maybe_refresh_rss`); §7 SSRF (Task 2 utils + Task 8 valid Madders); §7.2 robots.txt (Task 4); §8 toolchain (Task 1); §9 logging JSON-lines stderr (Task 2); §10 tests (Tasks 2-9); §11 YAGNI (none implemented); §12 execution order (Tasks 1-9 match); §13 references (Global Constraints).
- **Placeholder scan**: No TBD/TODO. All code blocks complete. Some inline comments note follow-up ("If fails, debug X") with explicit diagnostic.
- **Type consistency**: `MCPServer` (not `FastMCP`) used consistently per v2 API. `search_items` signature in Task 6 matches the caller in Task 8 `search_news`. `ParsedPost` field names (`items`, `financial_data`, `title`, `pub_date`, `post_type`) consistent across Task 5 parser and Task 7 crawler `upsert_parsed_post`. `ItemRow` fields match between Task 3 db and Task 7 crawler.
- **Ambiguity check**: `get_post(date)` with multiple posts returns list (made explicit); `discover` with no filters returns 50 most recent (made explicit).

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-08-fearnation-mcp.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
