"""Tests for robots.txt fetch/parse/cache."""

from __future__ import annotations

import httpx

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
            "User-agent: googlebot\nDisallow: /google-only/\n" "User-agent: *\nDisallow: /all/\n",
            base_url="https://fearnation.club/",
        )
        assert rules.is_allowed("/google-only/")
        assert not rules.is_allowed("/all/")

    def test_consecutive_user_agents_share_rules(self) -> None:
        rules = RobotsRules.from_text(
            "User-agent: *\nUser-agent: FearNationBot\nDisallow: /shared/\n"
            "User-agent: googlebot\nDisallow: /google-only/\n"
        )
        assert not rules.is_allowed("/shared/post")
        assert rules.is_allowed("/google-only/post")


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
