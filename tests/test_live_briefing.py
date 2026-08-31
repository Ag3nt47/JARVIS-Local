from __future__ import annotations

import httpx
import pytest

from jarvis.live_briefing import (
    FeedDefinition,
    LiveBriefingService,
    _parse_feed,
    format_json,
    word_count,
)

RSS = b"""<?xml version="1.0"?>
<rss><channel><item>
  <title>Markets &amp; policy</title>
  <description><![CDATA[<p>Bounded <b>summary</b>.</p>]]></description>
  <link>https://news.example/story</link>
  <pubDate>Sun, 24 Aug 2026 12:00:00 GMT</pubDate>
</item></channel></rss>
"""


def test_feed_parser_returns_bounded_plain_text() -> None:
    result = _parse_feed(RSS, "Example", 5)

    assert result == [
        {
            "source": "Example",
            "title": "Markets & policy",
            "summary": "Bounded summary .",
            "link": "https://news.example/story",
            "published": "Sun, 24 Aug 2026 12:00:00 GMT",
        }
    ]


@pytest.mark.asyncio
async def test_live_feed_rejects_redirect_outside_approved_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "approved.example":
            return httpx.Response(302, headers={"Location": "https://evil.example/feed"})
        return httpx.Response(200, content=RSS)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    try:
        items, error = await LiveBriefingService._fetch_feed(
            client,
            FeedDefinition("Approved", "https://approved.example/feed"),
        )
    finally:
        await client.aclose()

    assert items == []
    assert error is not None and "redirected outside" in error


def test_live_utilities_are_real_and_bounded() -> None:
    assert format_json('{"b": 2, "a": 1}')["formatted"] == '{\n  "a": 1,\n  "b": 2\n}'
    assert word_count("one two\nthree") == {"characters": 13, "words": 3, "lines": 2}
