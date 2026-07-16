"""robots.txt fetch + parse. Honors User-agent: * rules only.

Permissive default. If fetch fails (404 or network error), fall back
to DEFAULT_RULES (allow all).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
        current_agents: list[str] = []
        group_has_rules = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if group_has_rules:
                    current_agents = []
                    group_has_rules = False
                current_agents.append(value.lower())
            elif key == "disallow":
                group_has_rules = True
                if "*" in current_agents:
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
            last_fetched=datetime.now(UTC).isoformat(timespec="seconds"),
            raw_text=parsed.raw_text,
        )
        log.info(
            "robots.txt fetched",
            extra={
                "url": robots_url,
                "disallow_count": len(rules.disallow_paths),
            },
        )
        return rules
    except (httpx.HTTPError, OSError) as exc:
        log.warning(
            "robots.txt fetch failed, using default",
            extra={
                "url": robots_url,
                "error": str(exc),
            },
        )
        return DEFAULT_RULES
