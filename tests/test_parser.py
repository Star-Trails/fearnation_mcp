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
        html = _wrap("<h1>中国新闻</h1>" "<p><strong>• 测试标题</strong><br>测试正文</p>")
        result = parse_post("test-slug", html)
        assert len(result.items) == 1
        item = result.items[0]
        assert item.headline == "测试标题"
        assert item.body_text == "测试正文"
        assert item.section == "中国新闻"
        assert item.seq == 0

    def test_headline_strips_bullet(self) -> None:
        for bullet in ["•", "・", "‣", "·", "－", "—"]:
            html = _wrap(f"<h1>新闻</h1>" f"<p><strong>{bullet}标题</strong><br>正文</p>")
            result = parse_post("s", html)
            assert result.items[0].headline == "标题", f"failed for bullet {bullet!r}"


class TestMultipleItems:
    def test_multiple_items_in_section(self) -> None:
        html = _wrap(
            "<h1>中国新闻</h1>"
            "<p><strong>• 标题A</strong><br>正文A</p>"
            "<p><strong>• 标题B</strong><br>正文B</p>"
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].section == "中国新闻"
        assert result.items[0].seq == 0
        assert result.items[1].seq == 1

    def test_section_change_resets_section(self) -> None:
        html = _wrap(
            "<h1>中国新闻</h1>"
            "<p><strong>• 中国标题</strong><br>中国正文</p>"
            "<h1>印太新闻</h1>"
            "<p><strong>• 印太标题</strong><br>印太正文</p>"
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].section == "中国新闻"
        assert result.items[1].section == "印太新闻"

    def test_h2_section_header(self) -> None:
        html = _wrap("<h2>科技新闻</h2>" "<p><strong>• 标题</strong><br>正文</p>")
        result = parse_post("s", html)
        assert len(result.items) == 1
        assert result.items[0].section == "科技新闻"


class TestMultiParagraphBody:
    def test_following_p_appends_to_body(self) -> None:
        html = _wrap(
            "<h1>新闻</h1>"
            "<p><strong>• 标题</strong><br>第一段</p>"
            "<p>第二段继续</p>"
            "<p>第三段继续</p>"
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
            "<h1>新闻</h1>" "<p>不经 strong 的开场段落</p>" "<p><strong>• 标题</strong><br>正文</p>"
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        assert result.items[0].headline == ""
        assert "不经 strong 的开场段落" in result.items[0].body_text


class TestTagVariants:
    def test_b_tag(self) -> None:
        html = _wrap("<h1>新闻</h1><p><b>• 标题</b><br>正文</p>")
        assert parse_post("s", html).items[0].headline == "标题"

    def test_em_tag(self) -> None:
        html = _wrap("<h1>新闻</h1><p><em>• 标题</em><br>正文</p>")
        assert parse_post("s", html).items[0].headline == "标题"

    def test_no_bullet_entirely_bold(self) -> None:
        html = _wrap("<h1>新闻</h1><p><strong>缩影标题</strong></p>")
        assert parse_post("s", html).items[0].headline == "缩影标题"


class TestKoenigCardExclusion:
    def test_button_card_excluded(self) -> None:
        html = _wrap(
            "<h1>新闻</h1>"
            "<p><strong>• 标题</strong><br>正文</p>"
            '<div class="kg-card kg-button-card">'
            '<a href="https://example.com">点击支持1美元/年</a>'
            "</div>"
            "<p><strong>• 标题2</strong><br>正文2</p>"
        )
        result = parse_post("s", html)
        assert len(result.items) == 2
        for item in result.items:
            assert "点击支持" not in item.body_text
            assert "点击支持" not in item.headline

    def test_nested_paragraph_in_card_excluded(self) -> None:
        html = _wrap(
            "<h1>新闻</h1>"
            "<p><strong>• 标题</strong><br>正文</p>"
            '<div class="kg-card kg-callout-card">'
            "<p><strong>• 广告标题</strong><br>广告正文</p>"
            "</div>"
            "<p><strong>• 标题2</strong><br>正文2</p>"
        )
        result = parse_post("s", html)
        assert [item.headline for item in result.items] == ["标题", "标题2"]


class TestFinancialData:
    def test_ku_cha_shu_ju_block_extracts_financial_rows(self) -> None:
        html = _wrap(
            "<h1>苦茶数据</h1>"
            "<p>USD/CNH 7.2, USD/JPY 145.5, Brent $82</p>"
            "<p>BTC $42000, ETH $2500</p>"
            "<h1>中国新闻</h1>"
            "<p><strong>• 标题</strong><br>正文</p>"
        )
        result = parse_post("s", html)
        fields = {r.field for r in result.financial_data}
        assert "USD/CNH" in fields
        assert "BTC" in fields
        assert len(result.items) == 1
        assert result.items[0].section == "中国新闻"

    def test_digits_in_field_name_are_not_used_as_value(self) -> None:
        html = _wrap("<h1>苦茶数据</h1><p>沪深300 4000</p>")
        result = parse_post("s", html)
        assert [(row.field, row.value) for row in result.financial_data] == [("沪深300", "4000")]


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
            "<html><body>" "<h1>新闻</h1>" "<p><strong>• 标题</strong><br>正文</p>" "</body></html>"
        )
        result = parse_post("s", html)
        assert len(result.items) == 1

    def test_no_pub_date_returns_none(self) -> None:
        html = (
            "<html><body>"
            '<main class="post-content">'
            "<h1>新闻</h1>"
            "<p><strong>• x</strong><br>y</p>"
            "</main></body></html>"
        )
        result = parse_post("s", html)
        assert result.pub_date is None

    def test_invalid_pub_date_is_ignored(self) -> None:
        html = _wrap(
            "<h1>新闻</h1><p><strong>• 标题</strong><br>正文</p>",
            pub_date="2024-02-30",
        )
        assert parse_post("s", html).pub_date is None
