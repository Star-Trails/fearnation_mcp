# src/fearnation_mcp/utils.py
"""Slug validation, URL safety, ISO date validation, JSON-lines stderr logging."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_slug(slug: str) -> str:
    """Validate a Ghost slug. Raises ValueError on invalid input.

    Ghost slugs match: ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ (lowercase alnum + hyphens,
    no leading/trailing hyphen, no underscores).
    """
    if not _SLUG_RE.match(slug):
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


def validate_site_url(url: str) -> str:
    """Validate that an absolute URL is pinned to FearNation over HTTPS."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "fearnation.club":
        raise ValueError(f"URL must use https://fearnation.club: {url!r}")
    return url


def validate_iso_date(date_str: str) -> str:
    """Validate an ISO 8601 date string (YYYY-MM-DD)."""
    if not _ISO_DATE_RE.match(date_str):
        raise ValueError(f"Invalid ISO date: {date_str!r}")
    datetime.strptime(date_str, "%Y-%m-%d")
    return date_str


def validate_date_range(date_from: str | None, date_to: str | None) -> None:
    """Validate optional ISO dates and require an ascending inclusive range."""
    if date_from:
        validate_iso_date(date_from)
    if date_to:
        validate_iso_date(date_to)
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must be on or before date_to")


class _JsonLinesFormatter(logging.Formatter):
    """Format log records as JSON-lines to stderr."""

    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "ts",
        "level",
        "logger",
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


def make_http_client(**kwargs: object) -> httpx.Client:
    """Create an ``httpx.Client`` pinned to an IPv4 source address.

    Rationale: some user networks have broken IPv6 egress to Cloudflare
    edges (silent TLS resets on the IPv6 path). httpx does not fall back
    from IPv6 to IPv4 the way curl does, so the default stack fails
    outright on such networks. Pinning ``local_address='0.0.0.0'``
    forces IPv4 throughout, which is fine for FearNation's Cloudflare
    dual-stack endpoint (``A`` records are always reachable) and has
    no side effects on healthy dual-stack networks.

    Callers may override the transport by passing ``transport=``.
    """
    kwargs.setdefault("transport", httpx.HTTPTransport(local_address="0.0.0.0"))
    return httpx.Client(**kwargs)  # type: ignore[arg-type]
