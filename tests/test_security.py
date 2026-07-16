# tests/test_security.py
"""Tests for slug validation, URL safety, date validation."""

from __future__ import annotations

import pytest

from fearnation_mcp.utils import (
    build_post_url,
    validate_date_range,
    validate_iso_date,
    validate_site_url,
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
            "../",
            "..%2f",
            "//evil.com/x",
            "foo?bar=baz",
            "foo#anchor",
            "foo bar",
            "FooBar",
            "-leading-dash",
            "trailing-dash-",
            "",
            "with_underscore",
            "with/slash",
            "with:colon",
            "with(paren)",
            "with;semicolon",
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


class TestValidateSiteUrl:
    def test_accepts_pinned_https_origin(self) -> None:
        assert (
            validate_site_url("https://fearnation.club/sitemap-posts.xml")
            == "https://fearnation.club/sitemap-posts.xml"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "http://fearnation.club/sitemap.xml",
            "https://evil.example/sitemap.xml",
            "https://fearnation.club.evil.example/sitemap.xml",
            "https://user@fearnation.club/sitemap.xml",
        ],
    )
    def test_rejects_unpinned_origins(self, url: str) -> None:
        with pytest.raises(ValueError):
            validate_site_url(url)


class TestValidateIsoDate:
    def test_valid(self) -> None:
        assert validate_iso_date("2024-01-15") == "2024-01-15"

    @pytest.mark.parametrize(
        "bad",
        ["2024-1-15", "2024-01-32", "2024-02-30", "20240115", "not-a-date", "", "2024/01/15"],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_iso_date(bad)

    def test_rejects_reversed_date_range(self) -> None:
        with pytest.raises(ValueError, match="date_from"):
            validate_date_range("2024-02-01", "2024-01-01")


class TestJsonLinesLogger:
    def test_extra_fields_serialize(self, caplog: pytest.LogCaptureFixture) -> None:
        from fearnation_mcp.utils import get_logger

        logger = get_logger("test.extra")
        logger.warning("hello", extra={"url": "x", "status": 200, "latency_ms": 5})
        # No exception raised == pass.
