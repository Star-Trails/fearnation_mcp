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
from typing import cast

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from fearnation_mcp.utils import get_logger, validate_iso_date

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
    items: list[ParsedItem] = field(default_factory=lambda: list[ParsedItem]())
    financial_data: list[FinancialDataRow] = field(default_factory=lambda: list[FinancialDataRow]())


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
        for child in first.next_siblings:
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

    def _date_prefix(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value[:10]
        try:
            return validate_iso_date(candidate)
        except ValueError:
            return None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            loaded: object = json.loads(script.string or "")
            if isinstance(loaded, dict):
                data = cast(dict[str, object], loaded)
                if parsed := _date_prefix(data.get("datePublished")):
                    return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and (parsed := _date_prefix(meta.get("content"))):
        return parsed
    t = soup.find("time", attrs={"datetime": True})
    if t and (parsed := _date_prefix(t.get("datetime"))):
        return parsed
    return None


def _is_koenig_card(tag: Tag) -> bool:
    classes = tag.get("class")
    if isinstance(classes, str):
        return classes.startswith("kg-card")
    if isinstance(classes, list):
        return any(value.startswith("kg-card") for value in classes)
    return False


def _is_inside_koenig_card(tag: Tag) -> bool:
    """Return whether a tag is a Koenig card or is nested inside one."""
    return _is_koenig_card(tag) or any(_is_koenig_card(parent) for parent in tag.parents)


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
        # Search after the field so digits inside names such as ``沪深300``
        # are not mistaken for the quoted value.
        val_match = _FINANCIAL_VALUE_RE.search(tok, m.end())
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

    main = (
        soup.find(class_="post-content") or soup.find("main") or soup.find("article") or soup.body
    )
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
            items.append(
                ParsedItem(
                    section=current_item.section,
                    headline=current_item.headline,
                    body_text=full_body,
                    seq=current_item.seq,
                )
            )
        current_item = None
        current_body_parts = []

    for element in main.descendants:
        if not isinstance(element, Tag):
            continue
        if _is_inside_koenig_card(element):
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
        log.warning(
            "parser extracted 0 items and 0 financial rows",
            extra={
                "slug": slug,
                "anomalies": anomalies,
            },
        )
    elif anomalies:
        log.info(
            "parser completed with anomalies",
            extra={
                "slug": slug,
                "items_extracted": len(items),
                "financial_rows": len(financial),
                "anomalies": anomalies,
            },
        )

    return ParsedPost(
        title=title,
        pub_date=pub_date,
        post_type=post_type,
        items=items,
        financial_data=financial,
    )
