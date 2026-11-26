import asyncio
import base64
import io
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import aiohttp
from PIL import Image

from sources import (
    BANGUMI_CALENDAR,
    BILIBILI_HOTWORDS,
    COVER_LIMIT,
    HITOKOTO,
    ITHOME_RSS,
    UAPI,
    WORLD_NEWS,
    DailySources,
)

TODAY = date(2026, 11, 26)


class TestDailySources(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.sources = DailySources(self.session)

    async def test_closed_sections_perform_no_requests(self) -> None:
        with patch.object(self.sources, "_read", new_callable=AsyncMock) as request:
            result = await self.sources.collect(
                TODAY,
                anime_count=0,
                hot_count=0,
                news_count=0,
                tech_count=0,
                show_quote=False,
                show_word=False,
            )
        self.assertEqual(result, {})
        request.assert_not_awaited()

    async def test_collect_reports_independent_failure_and_skips_stale_news(
        self,
    ) -> None:
        async def json_for(url, **kwargs):
            if url == BANGUMI_CALENDAR:
                raise TimeoutError
            if url == WORLD_NEWS:
                return {"code": 200, "data": {"date": "2026-11-25", "news": ["旧闻"]}}
            if url == f"{UAPI}/saying":
                return {"text": "午后好\n愿你\u202e平安"}
            raise AssertionError(url)

        with (
            patch.object(self.sources, "_json", side_effect=json_for),
            self.assertLogs("sources", "WARNING"),
        ):
            result = await self.sources.collect(
                TODAY,
                anime_count=4,
                hot_count=0,
                news_count=5,
                tech_count=0,
                show_quote=True,
                show_word=False,
            )

        self.assertEqual(list(result), ["anime", "news", "quote"])
        self.assertEqual(result["anime"]["status"], "error")
        self.assertEqual(result["news"]["status"], "empty")
        self.assertEqual(result["news"]["items"], [])
        self.assertEqual(result["quote"]["items"][0]["title"], "午后好 愿你 平安")

    async def test_bangumi_falls_back_to_configured_proxy_and_keeps_items_without_covers(
        self,
    ) -> None:
        proxy = "http://someone:secret@127.0.0.1:7890"
        sources = DailySources(self.session, bangumi_proxy=proxy, show_covers=True)
        cover = "http://lain.bgm.tv/pic/cover/m/fc/d2/449346_ab.jpg"
        calendar = [
            {"weekday": {"id": 3}, "items": [{"name": "周三番剧"}]},
            {
                "weekday": {"id": 4},
                "items": [
                    {
                        "name_cn": "周四番剧",
                        "name": "原名",
                        "id": 42,
                        "images": {"medium": cover},
                    },
                    {"name": "无封面", "images": {"common": "https://127.0.0.1/a.png"}},
                ],
            },
        ]
        with (
            patch.object(
                sources, "_json", side_effect=[TimeoutError, calendar]
            ) as request,
            patch.object(
                sources,
                "_download_cover",
                side_effect=["data:image/jpeg;base64,abc", ""],
            ) as download,
            self.assertLogs("sources", "WARNING") as logs,
        ):
            result = await sources._anime(TODAY, 4)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [item["title"] for item in result["items"]], ["周四番剧", "无封面"]
        )
        self.assertEqual(result["items"][0]["url"], "https://bgm.tv/subject/42")
        self.assertEqual(result["items"][1]["cover"], "")
        self.assertEqual(
            [c.kwargs["proxy"] for c in request.await_args_list], [None, proxy]
        )
        self.assertEqual(
            [call.args for call in download.await_args_list],
            [(cover.replace("http:", "https:"), proxy), ("", proxy)],
        )
        self.assertNotIn("secret", " ".join(logs.output))

    async def test_bangumi_cover_timeout_keeps_completed_covers_and_all_titles(
        self,
    ) -> None:
        sources = DailySources(self.session, show_covers=True)
        calendar = [
            {
                "weekday": {"id": TODAY.isoweekday()},
                "items": [
                    {
                        "name": "已取得",
                        "images": {
                            "medium": "https://lain.bgm.tv/pic/cover/m/completed.jpg"
                        },
                    },
                    {
                        "name": "未取得",
                        "images": {
                            "medium": "https://lain.bgm.tv/pic/cover/m/slow.jpg"
                        },
                    },
                ],
            }
        ]

        async def cover_for(url, proxy):
            if url.endswith("/completed.jpg"):
                return "data:image/jpeg;base64,abc"
            await asyncio.Event().wait()

        with (
            patch("sources.COVER_BATCH_TIMEOUT", 0.05),
            patch.object(sources, "_json", return_value=calendar),
            patch.object(sources, "_download_cover", side_effect=cover_for),
        ):
            result = await sources._anime(TODAY, 4)
        self.assertEqual(
            [item["title"] for item in result["items"]], ["已取得", "未取得"]
        )
        self.assertTrue(result["items"][0]["cover"])
        self.assertFalse(result["items"][1]["cover"])

    async def test_bilibili_filters_unsafe_links_and_limits_titles(self) -> None:
        data = {
            "list": [
                {"title": "\n  "},
                {"title": "A" * 600, "url": "https://www.bilibili.com@evil.example/1"},
                {"title": "第二条", "url": "http://WWW.BILIBILI.COM/video/BV1"},
                {"title": "第三条", "url": "https://www.bilibili.com/video/BV2"},
            ]
        }
        with patch.object(self.sources, "_json", return_value=data):
            result = await self.sources._hot(2)
        self.assertEqual(len(result["items"]), 2)
        first, second = result["items"]
        self.assertEqual(len(first["title"]), 180)
        self.assertTrue(first["title"].endswith("…"))
        self.assertEqual(first["url"], "")
        self.assertEqual(second["url"], "https://www.bilibili.com/video/BV1")

    async def test_bilibili_falls_back_to_search_hotwords_after_uapi_failure(
        self,
    ) -> None:
        search = {"code": 0, "list": [{"show_name": "你 好"}]}
        with (
            patch.object(
                self.sources, "_json", side_effect=[ValueError, search]
            ) as request,
            self.assertLogs("sources", "WARNING"),
        ):
            result = await self.sources._hot(4)
        self.assertEqual(request.await_args_list[1].args, (BILIBILI_HOTWORDS,))
        self.assertEqual(result["source"], "哔哩哔哩热搜")
        self.assertTrue(
            result["items"][0]["url"].endswith("?keyword=%E4%BD%A0%20%E5%A5%BD")
        )

    async def test_current_news_strips_duplicate_numbering_and_keeps_source_link(
        self,
    ) -> None:
        data = {
            "code": 200,
            "data": {
                "date": TODAY.isoformat(),
                "news": [" 1、第一条", "2. 第二条"],
                "link": "https://mp.weixin.qq.com/s#rd",
            },
        }
        with patch.object(self.sources, "_json", return_value=data):
            result = await self.sources._news(TODAY, 1)
        self.assertEqual(
            result["items"],
            [
                {
                    "title": "第一条",
                    "meta": "",
                    "cover": "",
                    "url": "https://mp.weixin.qq.com/s#rd",
                },
            ],
        )

    async def test_tech_parses_real_rss_fields(self) -> None:
        xml = (
            '<rss version="2.0"><channel><item><title>IT &amp; 技术</title>'
            "<link>https://www.ithome.com/1/000/001.htm</link></item>"
            "<item><title>第二条</title><link>https://www.ithome.com/2</link></item></channel></rss>"
        ).encode()
        with patch.object(self.sources, "_read", return_value=xml) as request:
            result = await self.sources._tech(1)
        request.assert_awaited_once_with(ITHOME_RSS)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["items"][0]["title"], "IT & 技术")
        self.assertEqual(len(result["items"]), 1)

    async def test_tech_rejects_xml_doctype_and_uses_fallback(self) -> None:
        xml = b'<!DOCTYPE rss [<!ENTITY demonstration "expand">]><rss><channel/></rss>'
        fallback = {
            "list": [{"title": "科技热榜", "url": "https://www.ithome.com/top"}]
        }
        with (
            patch.object(self.sources, "_read", return_value=xml),
            patch.object(self.sources, "_json", return_value=fallback),
            self.assertLogs("sources", "WARNING"),
        ):
            result = await self.sources._tech(4)
        self.assertEqual(result["source"], "UAPI · IT之家热榜")
        self.assertEqual(result["items"][0]["title"], "科技热榜")

    async def test_quote_fallback_and_word_translation(self) -> None:
        fallback = {"hitokoto": "好好生活。", "from": "某作品", "from_who": "作者"}
        word = {
            "words": [{"word": "hello", "phonetic": "/həˈləʊ/", "translation": "你好"}]
        }
        with (
            patch.object(
                self.sources, "_json", side_effect=[ValueError, fallback, word]
            ) as request,
            self.assertLogs("sources", "WARNING"),
        ):
            quote_result = await self.sources._quote()
            word_result = await self.sources._word(TODAY)
        self.assertEqual(request.await_args_list[1].args, (HITOKOTO,))
        self.assertEqual(
            request.await_args_list[2].kwargs["params"]["date"], TODAY.isoformat()
        )
        self.assertEqual(quote_result["items"][0]["meta"], "某作品 · 作者")
        self.assertEqual(word_result["items"][0]["phonetic"], "/həˈləʊ/")
        self.assertEqual(word_result["items"][0]["meta"], "你好")

    async def test_cover_resizes_verified_raster_bytes(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (500, 750), "green").save(buffer, "PNG")
        with patch.object(self.sources, "_read", return_value=buffer.getvalue()):
            cover = await self.sources._download_cover(
                "https://lain.bgm.tv/a.png", None
            )
        prefix, payload = cover.split(",", 1)
        self.assertEqual(prefix, "data:image/jpeg;base64")
        with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(image.width, 192)
            self.assertLessEqual(image.height, 272)

    async def test_cover_never_follows_redirect_and_bounds_stream(self) -> None:
        class Response:
            status = 302
            content_type = "image/jpeg"
            content_length = None

            @property
            def content(self):
                return self

            async def iter_chunked(self, size):
                yield b"a" * COVER_LIMIT
                yield b"b"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        response = Response()
        with patch.object(self.session, "get", return_value=response) as request:
            self.assertEqual(
                await self.sources._download_cover("https://lain.bgm.tv/a.jpg", None),
                "",
            )
            self.assertIs(request.call_args.kwargs["allow_redirects"], False)
            response.status = 200
            self.assertEqual(
                await self.sources._download_cover("https://lain.bgm.tv/a.jpg", None),
                "",
            )


if __name__ == "__main__":
    unittest.main()
