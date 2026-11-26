import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

from calendar_data import build_calendar
from card import render_card, render_text_pages


def sample_report():
    day = date(2026, 11, 26)
    example_sections = {
        "news": (
            "60s API",
            ["这里展示当天早报的第一条摘要。", "各条信息按来源提供的顺序排列。"],
        ),
        "tech": (
            "IT之家 RSS",
            ["科技栏目展示最新的报道标题。", "可发送来源命令打开原文。"],
        ),
        "anime": (
            "Bangumi 番组计划",
            ["这里列出当日计划更新的动画。", "有封面时会自动显示缩略图。"],
        ),
        "hot": ("UAPI · 哔哩哔哩热门", ["这里显示当前热门视频标题。"]),
        "quote": ("UAPI 一言", ["风会把远方带到窗边。"]),
        "word": ("UAPI 每日单词", ["horizon"]),
    }
    sections = {}
    for key, (source, titles) in example_sections.items():
        sections[key] = {
            "status": "ok",
            "source": source,
            "items": [
                {"title": title, "url": "", "meta": "", "cover": ""} for title in titles
            ],
        }
    sections["word"]["items"][0]["phonetic"] = "/həˈraɪzən/"
    sections["word"]["items"][0]["meta"] = "地平线"
    return {"day": day, "calendar": build_calendar(day, 3), "sections": sections}


class CardTests(unittest.TestCase):
    def test_render_falls_back_from_an_unreadable_custom_portrait(self):
        report = sample_report()
        unreadable = Path(__file__)
        result = render_card(report, unreadable)
        self.assertEqual(result, render_card(report))
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.width, 960)
            self.assertGreater(image.height, image.width)
            image.verify()

    def test_links_keep_title_url_pairs_and_deduplicate_edition(self):
        report = sample_report()
        issue_url = "https://60s.viki.moe/read/1"
        for item in report["sections"]["news"]["items"]:
            item["url"] = issue_url
        sample = {"status": "ok", "source": "RSS", "items": []}
        for index in range(8):
            sample["items"].append(
                {
                    "title": f"第 {index} 条 @everyone",
                    "url": f"https://www.ithome.com/{index}" + "x" * 650,
                }
            )
        report["sections"]["tech"] = sample

        pages = render_text_pages(report, links_only=True)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(0 < len(page) <= 1500 for page in pages))
        self.assertEqual("\n".join(pages).count(issue_url), 1)
        self.assertNotIn("@everyone", "\n".join(pages))
        for item in sample["items"]:
            entry = f"{item['title'].replace('@', '＠')}\n{item['url']}"
            self.assertTrue(any(entry in page for page in pages))


if __name__ == "__main__":
    unittest.main()
