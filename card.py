from __future__ import annotations

import base64
import logging
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ASSETS = Path(__file__).resolve().parent / "assets"
LOGGER = logging.getLogger(__name__)
WIDTH = 760
GUTTER = 34
INSET = 22
INK = "#182D43"
MUTED = "#4D6474"
PAPER = "#F1F5F7"
LINE = "#E2EAEF"
WHITE = "#FFFFFF"
SECTION_TITLES = {
    "news": "今日早报",
    "tech": "科技资讯",
    "anime": "今日新番",
    "hot": "B站热门",
    "quote": "今日一言",
    "word": "每日英语",
}
SECTION_MARKS = {
    "news": ("#DDECFA", "#1D5588", "报"),
    "tech": ("#DCF2EE", "#175E56", "科"),
    "anime": ("#EDE9FA", "#51438B", "番"),
    "hot": ("#FBE8EE", "#923C5D", "热"),
    "quote": ("#FFF0E2", "#81502F", "言"),
    "word": ("#E9EFF8", "#3A527A", "EN"),
}
STATUS_TEXT = {"empty": "今天暂无条目", "error": "数据源暂不可用"}
_NO_LINE_START = '，。；、：！？》〉」』】〕）］｝”’…·,.?!)":;'


@lru_cache(maxsize=18)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSansSC-Regular.otf"), size)


@lru_cache(maxsize=1)
def _phonetic_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSans-Latin.ttf"), 20)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    value: str,
    size: int,
    color: str = INK,
    weight: int = 0,
) -> None:
    draw.text(
        (x, y), value, font=_font(size), fill=color, anchor="lt", stroke_width=weight
    )


def _draw_right(
    draw: ImageDraw.ImageDraw, x: int, y: int, value: str, size: int, color: str = MUTED
) -> None:
    draw.text((x, y), value, font=_font(size), fill=color, anchor="rt")


def _wrap(value: str, size: int, width: int, max_lines: int) -> list[str]:
    font = _font(size)
    tokens = re.findall(r"[A-Za-z0-9'-]+|.", " ".join(value.split()))
    lines = []
    line = ""
    for token in tokens:
        if font.getlength(line + token) <= width:
            line += token
            continue
        if token in _NO_LINE_START and line:
            token = line[-1] + token
            line = line[:-1]
        if line.strip():
            lines.append(line.strip())
        if font.getlength(token) <= width:
            line = token.lstrip()
            continue
        line = ""
        for char in token:
            if font.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
    if line.strip():
        lines.append(line.strip())
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1].rstrip()
    while font.getlength(last + "…") > width:
        last = last[:-1]
    return [*lines[: max_lines - 1], last + "…"]


def _tile(height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    tile = Image.new("RGB", (WIDTH - 2 * GUTTER, height), PAPER)
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle(
        (0, 0, tile.width - 1, height - 1), radius=22, fill=WHITE, outline=LINE, width=1
    )
    return tile, draw


def _tile_heading(
    tile: Image.Image, draw: ImageDraw.ImageDraw, key: str, source: str
) -> None:
    fill, accent, mark = SECTION_MARKS[key]
    draw.rounded_rectangle((INSET, 16, INSET + 42, 58), radius=12, fill=fill)
    draw.text(
        (INSET + 21, 37),
        mark,
        font=_font(15 if mark == "EN" else 21),
        fill=accent,
        anchor="mm",
    )
    _draw_text(draw, INSET + 58, 23, SECTION_TITLES[key], 29, weight=1)
    _draw_right(draw, tile.width - INSET, 27, source, 18)


def _cover(uri: str, size: tuple[int, int]) -> Image.Image | None:
    if not uri.startswith("data:image/jpeg;base64,"):
        return None
    try:
        with Image.open(
            BytesIO(base64.b64decode(uri.split(",", 1)[1], validate=True))
        ) as image:
            return ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def _anime_grid(section: dict) -> Image.Image:
    items = section["items"]
    tile, draw = _tile(82 + ((len(items) + 1) // 2) * 108)
    col_width = (tile.width - 2 * INSET - 12) // 2
    _tile_heading(tile, draw, "anime", section["source"])
    for index, item in enumerate(items):
        x = INSET + (index % 2) * (col_width + 12)
        y = 72 + (index // 2) * 108
        draw.rounded_rectangle((x, y, x + col_width, y + 96), radius=12, fill="#F3F1FC")
        cover = _cover(item.get("cover", ""), (60, 84))
        if cover:
            tile.paste(cover, (x + 6, y + 6))
        else:
            _draw_text(draw, x + 14, y + 37, str(index + 1).zfill(2), 23, "#51438B")
        for row, line in enumerate(_wrap(item["title"], 23, col_width - 88, 3)):
            _draw_text(draw, x + 76, y + 12 + row * 25, line, 23)
    return tile


def _panel(key: str, section: dict) -> Image.Image:
    items = section["items"]
    if key == "anime" and items and any(item.get("cover") for item in items):
        return _anime_grid(section)

    is_feature = key in {"quote", "word"}
    lines_by_item = []
    for item in items:
        lines = _wrap(
            item["title"],
            27,
            WIDTH - 2 * GUTTER - 2 * INSET - 45,
            3 if is_feature else 2,
        )
        details = (
            _wrap(item.get("meta", ""), 20, WIDTH - 2 * GUTTER - 2 * INSET - 45, 2)
            if is_feature
            else []
        )
        lines_by_item.append((lines, details, item.get("phonetic", "")))

    tile, draw = _tile(
        88
        + sum(
            len(lines) * 34 + (len(details) + bool(phonetic)) * 25 + 16
            for lines, details, phonetic in lines_by_item
        )
        if lines_by_item
        else 115
    )
    _tile_heading(tile, draw, key, section["source"])
    if not lines_by_item:
        _draw_text(
            draw, INSET, 76, STATUS_TEXT.get(section["status"], "暂无条目"), 23, MUTED
        )
        return tile

    y = 74
    for index, (lines, details, phonetic) in enumerate(lines_by_item):
        if is_feature:
            draw.rounded_rectangle(
                (INSET + 2, y + 4, INSET + 6, y + 29),
                radius=2,
                fill=SECTION_MARKS[key][1],
            )
        else:
            _draw_text(
                draw,
                INSET + 2,
                y + 4,
                str(index + 1).zfill(2),
                19,
                SECTION_MARKS[key][1],
            )
        for line in lines:
            _draw_text(draw, INSET + 43, y, line, 27)
            y += 34
        if phonetic:
            draw.text(
                (INSET + 43, y + 1),
                phonetic,
                font=_phonetic_font(),
                fill=MUTED,
                anchor="lt",
            )
            y += 25
        for line in details:
            _draw_text(draw, INSET + 43, y + 1, line, 20, MUTED)
            y += 25
        if index < len(lines_by_item) - 1:
            draw.line((INSET + 43, y + 5, tile.width - INSET, y + 5), fill=LINE)
        y += 16
    return tile


def _countdown(item: dict) -> str:
    return "今天" if item["days_left"] == 0 else f"{item['days_left']} 天后"


def _calendar_panel(festivals: list[dict]) -> Image.Image:
    tile, draw = _tile(72 + ((len(festivals) + 2) // 3) * 82)
    _draw_text(draw, INSET, 20, "周末与节日", 29, weight=1)
    col_width = (tile.width - 2 * INSET - 16) // 3
    for index, item in enumerate(festivals):
        x = INSET + (index % 3) * (col_width + 8)
        y = 58 + (index // 3) * 82
        draw.rounded_rectangle(
            (x, y, x + col_width - 1, y + 71), radius=12, fill="#F1F6FB"
        )
        _draw_text(draw, x + 12, y + 10, item["name"], 23)
        _draw_text(
            draw,
            x + 12,
            y + 40,
            f"{item['date'][5:].replace('-', '/')}  ·  {_countdown(item)}",
            19,
            MUTED,
        )
    return tile


def _hero(size: tuple[int, int], custom_path: Path | None) -> Image.Image:
    if custom_path is not None and custom_path.is_file():
        try:
            if custom_path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("file too large")
            with Image.open(custom_path, formats=("WEBP", "PNG", "JPEG")) as image:
                if image.width * image.height > 12_000_000:
                    raise ValueError("image too large")
                return ImageOps.fit(
                    image.convert("RGB"), size, Image.Resampling.LANCZOS
                )
        except (OSError, ValueError, Image.DecompressionBombError):
            LOGGER.warning("[美咲日报] 自定义封面读取失败，使用内置封面")
    with Image.open(ASSETS / "sky-hero.webp") as image:
        return ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)


def render_card(report: dict, custom_hero: Path | None = None) -> bytes:
    """Render the edition locally and return a JPEG suitable for messaging adapters."""
    calendar = report["calendar"]
    sections = report["sections"]
    tiles = [_calendar_panel(calendar["festivals"])] if calendar["festivals"] else []
    for key in SECTION_TITLES:
        if key in sections:
            tiles.append(_panel(key, sections[key]))

    hero_height = 278
    start = hero_height + 94
    gap = 14
    canvas = Image.new(
        "RGB", (WIDTH, start + sum(tile.height + gap for tile in tiles) + 20), PAPER
    )
    canvas.paste(_hero((WIDTH, hero_height), custom_hero), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((GUTTER, 64, GUTTER + 62, 70), radius=3, fill="#F7B4B3")
    _draw_text(draw, GUTTER, 90, "美咲日报", 70, "#FFFFFF", 1)
    _draw_text(draw, GUTTER + 3, 180, "MISAKI DAILY", 25, "#D8EBFE")
    draw.rectangle((0, hero_height, WIDTH, hero_height + 74), fill=WHITE)
    _draw_text(
        draw, GUTTER, hero_height + 19, report["day"].strftime("%Y.%m.%d"), 33, weight=1
    )
    _draw_right(
        draw,
        WIDTH - GUTTER,
        hero_height + 24,
        f"{calendar['weekday']}  ·  农历{calendar['lunar']}",
        23,
    )

    y = start
    for tile in tiles:
        canvas.paste(tile, (GUTTER, y))
        y += tile.height + gap

    with BytesIO() as buffer:
        canvas.save(
            buffer,
            format="JPEG",
            quality=91,
            optimize=True,
            progressive=True,
            subsampling=0,
        )
        return buffer.getvalue()


def render_text(report: dict, *, links_only: bool = False) -> str:
    day = report["day"].isoformat()
    calendar = report["calendar"]
    sections = report["sections"]
    parts = [f"美咲日报 · {day}"]
    if not links_only:
        parts[0] += f"\n{calendar['weekday']} · 农历{calendar['lunar']}"
        if calendar["festivals"]:
            lines = [
                f"{item['name']} · {item['date']} · {_countdown(item)}"
                for item in calendar["festivals"]
            ]
            parts.append("\n".join(["【周末与节日】", *lines]))

    for key, title in SECTION_TITLES.items():
        section = sections.get(key)
        if not section:
            continue
        lines = []
        if links_only:
            links = {}
            for item in section["items"]:
                if item.get("url"):
                    links.setdefault(
                        item["url"], "本期早报原文" if key == "news" else item["title"]
                    )
            lines = [
                f"{label.replace('@', '＠')}\n{url.replace('@', '%40')}"
                for url, label in links.items()
            ]
            if not lines:
                continue
        else:
            for item in section["items"]:
                title_text = item["title"]
                if key == "word" and item.get("phonetic"):
                    title_text += f" · {item['phonetic']}"
                if key in {"quote", "word"} and item.get("meta"):
                    title_text += f" · {item['meta']}"
                lines.append(f"• {title_text.replace('@', '＠')}")
            if not lines:
                lines = [STATUS_TEXT.get(section["status"], "暂无条目")]
        parts.append("\n".join([f"【{title}】{section['source']}", *lines]))

    if links_only and len(parts) == 1:
        parts.append("当前栏目没有可用的原文链接。")
    return "\n\n".join(parts)


def render_text_pages(report: dict, *, links_only: bool = False) -> list[str]:
    """Keep a complete headline and URL together within common adapter text limits."""
    text = render_text(report, links_only=links_only)
    units = re.split(r"\n(?!https://)", text)
    pages = []
    current = ""
    for unit in units:
        if len(current) + len(unit) + 1 > 1500:
            pages.append(current.rstrip())
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    pages.append(current.rstrip())
    return pages
