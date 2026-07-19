# FearNation MCP 设计方案

- **日期**：2026-07-08
- **状态**：已批准，待 writing-plans 生成实施计划
- **目标读者**：实施者（fixer / 直接执行）、未来维护者

## 1. 目标

为 [fearnation.club](https://fearnation.club/)（中文政经新闻聚合，Ghost 6.51 站点）构建一个本地 MCP 服务，支持**按新闻条目粒度**检索「世界苦茶」每日摘要与「台海危機 ALERT」深度分析。

**用途**：思考政经内容前调用展开知识背景、或在特定节点查询特定新闻。

## 2. 数据源

站点为 Ghost 6.51 架构，用户非站长 → **Ghost Content API Key 不可用**（已实测确认）。

采用三层公开数据源：

| 源 | 用途 | 备注 |
|---|---|---|
| `/rss/` | 近期约 15 篇文章 + 增量 | RSS 2.0，`<content:encoded>` 含完整正文 HTML |
| `/sitemap-posts.xml` | 全量历史文章 slug 清单 | 只有 `<loc>` + `<lastmod>`，**无标题/发布日期** |
| `/<slug>/` HTML | 单篇完整正文 | 解析入口 |

**关键限制**：sitemap 无标题/发布日期，是放弃懒加载、改用全量爬取的根本原因——`discover("稀土")` 无法匹配中文内容（sitemap 只有 romanized slug）。

## 3. 架构：全量爬取 + RSS 增量

**核心策略**：首次启动时一次性全量爬取全部历史文章，之后 RSS 负责增量。

- 规模：约 300 篇 × 1 req/sec ≈ 5 分钟，DB 约 30-40MB。
- **不阻塞启动**：索引到哪服务到哪，crawl 作为后台任务带重试。
- **自愈**：启动时 re-parse `parsed_at IS NULL OR parsed_at < lastmod` 的 post，失败自动重试。
- **增量扩展**：RSS 抓新文章；sitemap 每周 sweep `lastmod` 变化，重抓改动过的旧文。
- 优雅处理 `sitemapindex` 递归（虽当前未拆分，但 +10 行 future-proof）。

全量爬取消除了懒加载的三个问题：旧文标题中文匹配、懒加载无限 fetch 风险、discover 二等 UX 体验。

## 4. 检索：SQLite + FTS5 + OpenCC

### 4.1 存储

- **SQLite**：标准库 `sqlite3`，**WAL 模式**（支持多只读客户端）。
- **路径**：`XDG_CACHE_HOME/fearnation_mcp/fearnation.db`（默认 `~/.cache/fearnation_mcp/`）。
- **目录权限**：0700。
- 全量爬取后内容可重建（`rm -rf ~/.cache/fearnation_mcp/` 重启即重新爬取）。

### 4.2 Schema

```sql
-- 文章元数据 + raw_html
posts(
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  pub_date TEXT,                  -- ISO 8601 date，来自 post HTML (JSON-LD / <time> / RSS pubDate)
  post_type TEXT,                 -- '世界苦茶' | '台海危機ALERT'
  raw_html TEXT,                  -- 完整正文 HTML，用于重解析
  parsed_at TEXT,                 -- ISO timestamp 上次解析时间
  lastmod TEXT,                   -- 来自 sitemap 的 <lastmod>
  last_seen TEXT                  -- ISO timestamp 上次 fetch 时间
);

-- 新闻条目（检索粒度）
items(
  id INTEGER PRIMARY KEY,
  post_slug TEXT NOT NULL REFERENCES posts(slug) ON DELETE CASCADE,
  section TEXT,                   -- 中国新闻 / 印太新闻 / 科技新闻 / 经济新闻 / 其他
  headline TEXT,                  -- 原文脚本
  headline_norm TEXT,             -- OpenCC t2s 归一化简体（FTS5 索引此列）
  body_text TEXT,                  -- BeautifulSoup get_text() 纯文本
  body_norm TEXT,                 -- OpenCC t2s 归一化简体（FTS5 索引此列）
  seq INTEGER,                    -- 文档内顺序，用于稳定排序
  pub_date TEXT                   -- 继承自 post.pub_date（冗余，便于 item 级过滤），见 §4.4
);
CREATE INDEX idx_items_pub_date ON items(pub_date);
CREATE INDEX idx_items_post_slug ON items(post_slug);

-- 金融数据（苦茶数据块）
financial_data(
  id INTEGER PRIMARY KEY,
  post_slug TEXT NOT NULL REFERENCES posts(slug) ON DELETE CASCADE,
  field TEXT,                     -- 'USD/CNH', 'Brent', 'BTC' 等
  value TEXT
);

-- 全文索引（普通表非外部内容表，避免 sync 坑）
CREATE VIRTUAL TABLE items_fts USING fts5(
  headline_norm, body_norm,
  tokenize='unicode61 remove_diacritics 2'
);
-- 注：采用普通 FTS5 表（不走外部内容表 content=... 模式）
-- 在 per-post txn 内双写 items + items_fts，详见 §4.3

-- 站点级元数据
meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
-- keys: last_rss_fetch, last_sitemap_sweep, schema_version, robots_rules_json, full_crawl_done
```

**取消 `sitemap_urls` 表**：全量爬取后角色折叠进 `posts`。

### 4.3 写入策略

- **txn-per-post 幂等 upsert**：`ON CONFLICT(slug) DO UPDATE`。
- 单个 txn 内双写：`posts` 行 + 全部 `items` 行 + 全部 `financial_data` 行 + 全部 `items_fts` 行（FTS5 用普通表，content 表不走外部内容表模式以避免 sync 坑）。
- 重跑安全。

### 4.4 Item 级 pub_date（关键决策）

**用户决策（m0035-m0037）**：每个 item 冗余写入 `pub_date`（继承自所属 post 的 `pub_date`），便于：

- `search_news(date_from, date_to)` 直接用 `WHERE items.pub_date BETWEEN ? AND ?` **无需 JOIN** posts。
- 返回结果每个 item 携带自己的 `pub_date`，AI 可直接定位「这条新闻是哪天的」。
- post 的 `pub_date` 变更（重解析）时同步更新归属 item 的 `pub_date`。

`discover` 仍按 post 级日期范围浏览（`posts.pub_date`）。

### 4.5 检索质量关键决策

#### Tokenizer：unicode61 + remove_diacritics 2（不用 jieba）

- 规模仅约 300 篇 × 35 条 ≈ 10,500 行，unicode61 的隐式短语匹配（`MATCH '稀土'` tokenized 为 `['稀', '土']` 隐式短语匹配，要求相邻）对「稀土」「台海军演」足够。
- jieba 在繁体文本上反而加噪声。
- 真正影响的不是分词器，而是简繁脚本不匹配问题（见下）。

#### OpenCC 归一化（最高 ROI 单点）

- 站点简繁混用：「台海危機 ALERT」繁体标题、「世界苦茶」简体正文。
- 不加 OpenCC 的话，简体查询「稀土开采」会**静默零结果**匹配繁体「稀土開採」内容。
- Schema 已加 `headline_norm` / `body_norm` 列（OpenCC `t2s` 转换），FTS5 索引 `_norm` 列。
- 查询时先 OpenCC 归一化 query string 再 `MATCH`。
- 显示用 `headline` / `body_text` 保留原文脚本。

## 5. 解析器

**permissive DOM 驱动**（BeautifulSoup4 + lxml），**不写 regex-on-plaintext fallback**。

### 5.1 算法

1. 从 `raw_html` 提取正文容器（Ghost 主题统一）。
2. 顺 DOM 序遍历，跟踪 current section（最近的 `<h1>` 文本 → 映射到「中国新闻 / 印太新闻 / 科技新闻 / 经济新闻 / 其他」）。
3. 每个 `<p>`：
   - 若以 `<strong>` / `<b>` / `<em>` 开头且文本起始是 bullet 字符 → 启动新 item（headline = bold 文本，body = 该 `<p>` 余下部分）。
   - 否则 → 追加到当前 item 的 body（无当前 item 则建空-headline item，**不丢弃**）。
4. body 持续累积至下一个 headline-`<p>`、下一个 `<h1>`、或 `苦茶数据` 边界。
5. `苦茶数据` 块单独 handler，抽入 `financial_data(post_id, field, value)` 表。

### 5.2 防御变体

- **Bullet 字符**：`•` / `・` / `‣` / `·` / `－` / `—` / 缺失 bullet / 前导数字或破折号。
- **Tag 变体**：`<strong>` / `<b>` / `<em>`、或 bold via inline style。
- **多段 body**：body = headline `<p>` + 后续非 headline `<p>` 直到下一个 headline 或 section。
- **Orphan `<p>`**：无 `<strong>` 的 `<p>`，附前一个 item 的 body 或建空-headline item。
- **Ghost Koenig 卡片**：`class` 前缀 `kg-card`，**排除** CTA 卡片（如「点击支持」）、bookmark/gallery/code 卡片。

### 5.3 异常处理

- 日志记录「section 0 items / post 0 items / bullet 未识别次数」+ slug → stderr。
- 保留 `raw_html` 以便结构漂移后重解析，免重抓。
- **永不 break MCP**——降级提取而非崩溃。

## 6. 工具面（4 个）

| 工具 | 用途 |
|---|---|
| `search_news(query, section?, date_from?, date_to?, limit?)` | 核心 FTS5 item 检索，覆盖全语料。OpenCC 归一化 query 后 MATCH。`date_from`/`date_to` 过滤 `items.pub_date`。 |
| `get_post(slug \| date)` | 取整篇 digest：全部 item + financial_data。 |
| `list_recent(days=7)` | 近期 post 标题 + 日期 + item 数。Agent orient 工具。 |
| `discover(query?, post_type?, date_from?, date_to?)` | Post 级目录浏览：按 title 子串 / `post_type` / 日期范围。全量爬取后是本地查询，cost 可忽略。 |

### 6.1 隐式 RSS 刷新（删除独立 `refresh_feed`）

其他工具调用前检查 `meta.last_rss_fetch` >60 min，则自动后台刷新 RSS。Agent 不需要思考新鲜度。

可选 v2 提供 `refresh_feed(force=true)` 显式逃生口，但 v1 不暴露。

### 6.2 工具返回约定

- 返回 **`BeautifulSoup.get_text()` 纯文本**而非 HTML（降 token 噪音、消除 XSS、缩结果体积）。
- `raw_html` **仅存储**用于重解析，不直接返回给 AI。
- `search_news` 返回 item 结果带 provenance：`slug` / `section` / `seq` / `pub_date`。

## 7. 安全与合规

### 7.1 SSRF 防护

- `get_post(slug)` 严格校验 slug：正则 `^[a-z0-9][a-z0-9-]*$`（Ghost slug 格式）。拒绝 `../` / `//evil.com/x` / `?` / `#` / 非 slug 字符。
- Host pinning：用 `urllib.parse.urljoin('https://fearnation.club/', slug + '/')` 后 assert `netloc == 'fearnation.club'` 和 `scheme == 'https'`。Defense-in-depth。
- `discover` 的日期参数校验 ISO 8601 格式（`YYYY-MM-DD`）。

### 7.2 robots.txt

- 启动时和每周 fetch 一次 `https://fearnation.club/robots.txt`，解析并拒绝 Disallowed 路径。
- 若全站禁爬则优雅退出。
- Ghost 默认 robots.txt 允许 `/rss/`、`/sitemap.xml`、`/<slug>/`（这些是公开内容机读设计意图）。

### 7.3 HTML 输出归一化

返回纯文本 eliminates XSS consideration（见 §6.2）。

## 8. 工具链与项目结构

### 8.1 选型

| 维度 | 选型 | 理由 |
|---|---|---|
| 包管理 | `uv` | 现代 Python 标配 |
| 打包 | `pyproject.toml` + hatchling | `[project.scripts]` entry：`fearnation-mcp = 'fearnation_mcp.__main__:main'` |
| Lint | `ruff` | 单工具替 flake8 + isort |
| 格式化 | `black` | 配合 ruff |
| 类型检查 | `pyright` strict 模式 | MCP 工具需严格类型 |
| MCP SDK | 官方 `mcp` Python SDK | stdio transport |
| 解析 | `beautifulsoup4` + `lxml` | DOM 驱动 |
| RSS | `feedparser` | RSS 2.0 标准 |
| OpenCC | `opencc` Python 绑定 | `t2s` 转换 |
| HTTP | `httpx` | 同步 + 异步 |
| 测试 | `pytest` | Python 标配 |

### 8.2 目录结构

```
fearnation-mcp/
├── pyproject.toml          # uv + hatchling
├── uv.lock
├── src/fearnation_mcp/
│   ├── __init__.py
│   ├── __main__.py         # CLI entry point
│   ├── server.py           # MCP server + tool 注册
│   ├── crawler.py          # sitemap 递归 + RSS + 全量爬取
│   ├── parser.py           # DOM 解析器
│   ├── db.py               # SQLite schema + 查询
│   ├── search.py           # FTS5 + OpenCC 检索封装
│   ├── robots.py           # robots.txt 处理
│   └── utils.py            # slug 校验、URL 安全、日志
├── tests/
│   ├── fixtures/
│   │   ├── posts/*.html    # 3-5 真实文章
│   │   └── rss.xml
│   ├── test_parser.py
│   ├── test_search.py
│   ├── test_crawler.py
│   └── test_security.py
└── .github/workflows/      # CI（可选）
```

### 8.3 代码规范

- 导入分组：标准库 / 第三方 / 本地应用，组间空行。
- `from __future__ import annotations`。
- 模块级 docstring 说明用途。
- 完整类型注解（pyright strict）。

### 8.4 MCP 客户端配置

MCP 客户端配置用 venv python **绝对路径**而非 PATH（稳定跨 shell 环境）。

### 8.5 依赖锁定

```toml
[project]
dependencies = [
    "mcp>=1.0",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "feedparser>=6.0",
    "opencc>=1.1",
    "httpx>=0.27",
]
```

## 9. 日志

**structured JSON-lines 到 stderr**（MCP stdio transport 的 stdout 保留给 JSON-RPC）。

事件类型：

- **fetch**：`url` / `status` / `bytes` / `latency_ms`
- **parse**：`slug` / `items_extracted` / `sections_found` / `anomalies`
- **cache**：hits / misses
- **FTS 查询**：`query` / `filters` / `result_count` / `latency_ms`

## 10. 测试策略

| 层 | 内容 | 方式 |
|---|---|---|
| **解析器单元**（最高 ROI） | Item 提取、section 分配、bullet 变体容忍、多段 body、orphan `<p>`、Koenig 卡片排除、`苦茶数据` 块 | 内联 HTML snippets |
| **Fixture-based**（高 ROI） | 3-5 真实保存文章：一篇「世界苦茶」digest、一篇「台海危機 ALERT」、一 edge-case | `tests/fixtures/posts/*.html` 入库 |
| **RSS 解析** | 保存的 `/rss/` XML → posts/items 插入 | `tests/fixtures/rss.xml` |
| **FTS 查询构建 + 质量** | 日期/section 过滤；语义断言；**跨脚本 OpenCC 断言**（简体查询匹配繁体内容） | 内存 `:memory:` SQLite + FTS5 |
| **Sitemapindex 递归** | 假 `<sitemapindex>` → 子 `<urlset>` | 内联 XML |
| **Slug 校验 / SSRF** | `../`、`evil.com/x`、`?`、`#`、非 slug 字符全拒 | 单元 |
| **Live network** | RSS/sitemap 可达性 | `@pytest.mark.network` 默认 skip，发布前手动跑 |

解析器和 FTS 测试是重点投入区。Live-integration 测试 brittle，加 confidence 有限，每季度或见 parser-anomaly 日志时 refetch fixtures 检测漂移。

## 11. 非 v1（明确延迟，YAGNI）

- ❌ **向量检索 / ONNX 嵌入模型**：FTS5 + OpenCC 已覆盖中文检索；规模仅 10K 行。
- ❌ **记忆提取机制**（decision/preference/constraint 自动抽取）：ctxgrep 作为「项目笔记」场景的需求，FearNation 是新闻聚合检索无对应需求。
- ❌ **Agent Skill 打包**（`skills/fearnation/SKILL.md`）：MCP 本身已是工具接口。
- ❌ **`get_financial_data(date)` 独立工具**：v1 通过 `get_post` 暴露 financial_data 即可。
- ❌ **jieba 分词器**：unicode61 在此规模足够，jieba 反而加繁体噪声。
- ❌ **`refresh_feed` 显式工具**：v1 隐式自动刷新，见 §6.1。

## 12. 执行顺序（待 writing-plans 细化）

1. `git init`（已完成）+ 目录骨架 + `pyproject.toml` + uv 环境
2. `db.py` schema 初始化
3. `robots.py` + `utils.py`（slug 校验、URL 安全、日志）
4. `parser.py` + 测试（最高 ROI 投入区）
5. `crawler.py`（sitemap 递归 + RSS + 全量爬取 + 重试）
6. `search.py`（FTS5 + OpenCC 封装）+ 检索质量测试
7. `server.py` + 4 个 MCP 工具实现
8. 3-5 真实 HTML fixture + FTS 质量 + SSRF 全套测试
9. 端到端 smoke test（`@network` 默认 skip）
10. 文档（README + 配置说明）

---

## 变更日志

- 2026-07-08 初稿：基于 `(b1)` oracle 审查 + 用户讨论决策定型
- 2026-07-08 修订：item-level `pub_date` 冗余写入（m0035-m0037 用户决策），见 §4.4
