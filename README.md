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

### `search_news(query, section?, date_from?, date_to?, limit?, mode="and")`

Full-text search over the indexed news items. Returns matches with `slug`,
`section`, `headline`, `body`, `pub_date`, `seq`.

- `mode="and"` (default): every whitespace-delimited keyword must match,
  e.g. `稀土 供应链` matches an item containing both terms anywhere.
- `mode="phrase"`: the complete query must match in the given order,
  e.g. `稀土 供应链` only matches that exact phrase.

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

## Contributors

- **[Star-Trails](https://github.com/Star-Trails)** — author, maintainer
- **[OpenCode](https://github.com/sst/opencode)** — AI coding assistant used throughout development (spec design, TDD implementation, code review)

## License

MIT — see [LICENSE](LICENSE).
