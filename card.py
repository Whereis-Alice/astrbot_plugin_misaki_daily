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
WIDTH = 960
MARGIN = 27
CONTENT_X = 54
CONTENT_W = WIDTH - 2 * CONTENT_X
GAP = 18
INK = "#302735"
MUTED = "#716071"
ACCENT = "#9A4D77"
EDGE = "#C78EAD"
PAPER = "#F8F2F6"
PANEL = "#FBF6F9"
LABEL = "#FFFBFD"
SECTION_TITLES = {
    "hot": "B站热点",
    "anime": "今日新番",
    "news": "60s读懂世界",
    "tech": "IT资讯",
    "quote": "今日一言",
    "word": "每日英语",
}
LABELS = {"calendar": "摸鱼日历", **SECTION_TITLES}
ICONS = {
    "calendar": "fish.png",
    "hot": "bilibili.png",
    "anime": "bgm.png",
    "news": "60.png",
    "tech": "it.png",
    "quote": "hitokoto.png",
}
STATUS_TEXT = {"empty": "今天暂无条目", "error": "数据源暂不可用"}
_NO_LINE_START = '，。；、：！？》〉」』】〕）］｝”’…·,.?!)":;'


@lru_cache(maxsize=18)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSansSC-Regular.otf"), size)


@lru_cache(maxsize=1)
def _phonetic_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSans-Latin.ttf"), 23)


@lru_cache(maxsize=len(ICONS))
def _section_icon(key: str) -> Image.Image:
    with Image.open(ASSETS / "icons" / ICONS[key]) as icon:
        return ImageOps.contain(
            icon.convert("RGBA"), (31, 31), Image.Resampling.LANCZOS
        )


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    size: int,
    color: str = INK,
    *,
    anchor: str = "lt",
    weight: int = 0,
) -> None:
    draw.text(
        xy, value, font=_font(size), fill=color, anchor=anchor, stroke_width=weight
    )


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


def _frame(
    key: str, width: int, height: int, source: str = ""
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (1, 28, width - 2, height - 2), radius=18, fill=PANEL, outline=EDGE, width=4
    )
    label = LABELS[key]
    label_right = 83 + round(_font(27).getlength(label)) + 16
    draw.rounded_rectangle(
        (19, 1, label_right, 55), radius=15, fill=LABEL, outline=EDGE, width=3
    )
    if key in ICONS:
        icon = _section_icon(key)
        image.paste(
            icon, (31 + (31 - icon.width) // 2, 12 + (31 - icon.height) // 2), icon
        )
    else:
        draw.rounded_rectangle((31, 12, 62, 43), radius=7, fill="#F1E1ED")
        _text(draw, (47, 28), "EN", 14, ACCENT, anchor="mm", weight=1)
    _text(draw, (72, 16), label, 27, ACCENT, weight=1)
    if source and label_right + _font(17).getlength(source) + 45 < width:
        _text(draw, (width - 22, 43), source, 17, MUTED, anchor="rt")
    return image, draw


def _countdown(item: dict) -> str:
    return "今天" if item["days_left"] == 0 else f"{item['days_left']} 天后"


def _calendar(festivals: list[dict], width: int, height: int) -> Image.Image:
    image, draw = _frame("calendar", width, height)
    for index, item in enumerate(festivals):
        y = 83 + index * 49
        _text(draw, (24, y), item["name"], 25, ACCENT, weight=1)
        _text(
            draw, (width - 24, y + 2), _countdown(item), 23, INK, anchor="rt", weight=1
        )
        if index < len(festivals) - 1:
            draw.line((24, y + 34, width - 24, y + 34), fill="#EBDCE6")
    return image


def _headline_rows(
    section: dict, width: int, size: int, marker: int, *, limit: int
) -> list[list[str]]:
    return [
        _wrap(item["title"], size, width - 48 - marker, limit)
        for item in section["items"]
    ]


def _list_panel(
    key: str, section: dict, width: int, *, min_height: int = 0
) -> Image.Image:
    hot = key == "hot"
    marker = 42 if hot else 30
    size, line_height, spacing = (24, 32, 13) if hot else (26, 38, 15)
    rows = _headline_rows(section, width, size, marker, limit=3 if hot else 4)
    height = 86 + sum(len(row) * line_height + spacing for row in rows) + 16
    if not rows:
        height = 140
    image, draw = _frame(key, width, max(height, min_height), section["source"])
    if not rows:
        _text(draw, (24, 85), STATUS_TEXT.get(section["status"], "暂无条目"), 24, MUTED)
        return image

    y = 80
    for index, row in enumerate(rows):
        if hot:
            color, label = ("#FF6868", "热") if index < 4 else ("#9C5529", "荐")
            draw.rounded_rectangle((24, y + 1, 54, y + 31), radius=7, fill=color)
            _text(draw, (39, y + 16), label, 19, "#FFFFFF", anchor="mm")
        else:
            draw.ellipse((27, y + 11, 34, y + 18), fill=ACCENT)
        for line in row:
            _text(draw, (24 + marker, y), line, size)
            y += line_height
        y += spacing
    return image


def _cover(uri: str, size: tuple[int, int]) -> Image.Image | None:
    if not uri.startswith("data:image/jpeg;base64,"):
        return None
    try:
        with Image.open(
            BytesIO(base64.b64decode(uri.split(",", 1)[1], validate=True)),
            formats=("JPEG",),
        ) as image:
            return ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def _anime_panel(section: dict, show_covers: bool) -> Image.Image:
    items = section["items"]
    if not show_covers or not items:
        return _list_panel("anime", section, CONTENT_W)

    columns = 5
    gap = 16
    poster_w = (CONTENT_W - 48 - gap * (columns - 1)) // columns
    poster_h = round(poster_w * 1.5)
    row_height = poster_h + 100
    height = 79 + ((len(items) + columns - 1) // columns) * row_height
    image, draw = _frame("anime", CONTENT_W, height, section["source"])
    mask = Image.new("L", (poster_w, poster_h))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, poster_w - 1, poster_h - 1), radius=11, fill=255
    )
    for index, item in enumerate(items):
        x = 24 + (index % columns) * (poster_w + gap)
        y = 78 + (index // columns) * row_height
        poster = _cover(item.get("cover", ""), (poster_w, poster_h))
        if poster is None:
            draw.rounded_rectangle(
                (x, y, x + poster_w - 1, y + poster_h - 1), radius=11, fill="#F1E4EE"
            )
            _text(
                draw,
                (x + poster_w // 2, y + poster_h // 2),
                "暂无封面",
                18,
                MUTED,
                anchor="mm",
            )
        else:
            image.paste(poster, (x, y), mask)
        for line_index, line in enumerate(_wrap(item["title"], 20, poster_w, 3)):
            _text(
                draw,
                (x + poster_w // 2, y + poster_h + 12 + line_index * 24),
                line,
                20,
                anchor="mt",
            )
    return image


def _feature(key: str, section: dict) -> Image.Image:
    if not section["items"]:
        return _list_panel(key, section, CONTENT_W)
    item = section["items"][0]
    quote = key == "quote"
    title = f"“{item['title']}”" if quote else item["title"]
    lines = _wrap(title, 29, CONTENT_W - 75, 4)
    meta = _wrap(item.get("meta", ""), 22, CONTENT_W - 84, 2)
    phonetic = item.get("phonetic", "")
    height = 106 + len(lines) * 38 + (len(meta) + bool(phonetic)) * 31
    image, draw = _frame(key, CONTENT_W, height, section["source"])
    y = 82
    for line in lines:
        xy = (CONTENT_W // 2, y) if quote else (26, y)
        _text(draw, xy, line, 29, ACCENT, anchor="mt" if quote else "lt", weight=1)
        y += 38
    if phonetic:
        draw.text((26, y), phonetic, font=_phonetic_font(), fill=MUTED, anchor="lt")
        y += 31
    for index, line in enumerate(meta):
        xy = (CONTENT_W - 30, y) if quote else (26, y)
        prefix = "—— " if quote and index == 0 else ""
        _text(draw, xy, prefix + line, 22, MUTED, anchor="rt" if quote else "lt")
        y += 31
    return image


def _portrait(path: Path | None) -> Image.Image | None:
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("file too large")
        with Image.open(path, formats=("WEBP", "PNG", "JPEG")) as image:
            if image.width * image.height > 12_000_000:
                raise ValueError("image too large")
            portrait = image.convert("RGBA")
            portrait = portrait.crop(
                portrait.getbbox(alpha_only=True) or (0, 0, *portrait.size)
            )
            portrait.thumbnail((210, 215), Image.Resampling.LANCZOS)
            return portrait
    except (OSError, ValueError, Image.DecompressionBombError):
        LOGGER.warning("[美咲日报] 自定义立绘读取失败")
        return None


def _masthead(canvas: Image.Image, report: dict, custom_portrait: Path | None) -> None:
    draw = ImageDraw.Draw(canvas)
    for y in range(59, 307, 9):
        for x in range(57 + (y % 2) * 4, WIDTH - 59, 9):
            draw.point((x, y), fill="#E8D9E4")

    portrait = _portrait(custom_portrait) or _portrait(ASSETS / "portrait.webp")
    if portrait is not None:
        canvas.paste(
            portrait,
            (61 + (218 - portrait.width) // 2, 272 - portrait.height),
            portrait,
        )
    else:
        draw.ellipse((94, 91, 239, 236), outline="#DEC3D6", width=2)
        draw.line(
            [
                (98, 201),
                (225, 122),
                (192, 223),
                (164, 194),
                (139, 216),
                (149, 183),
                (98, 201),
            ],
            fill="#BD85A8",
            width=4,
            joint="curve",
        )
        draw.line(
            [(149, 183), (225, 122), (164, 194)], fill="#BD85A8", width=3, joint="curve"
        )

    title_xy = (468, 92)
    font = _font(149)
    draw.text(
        (title_xy[0] + 7, title_xy[1] + 8),
        "日报",
        fill="#B7789D",
        font=font,
        anchor="mt",
        stroke_width=9,
    )
    draw.text(
        title_xy,
        "日报",
        fill="#D18EB0",
        font=font,
        anchor="mt",
        stroke_width=9,
        stroke_fill=LABEL,
    )
    draw.text(title_xy, "日报", fill="#D18EB0", font=font, anchor="mt", stroke_width=4)

    date_x = WIDTH - CONTENT_X - 205
    right = WIDTH - CONTENT_X
    center = (date_x + right) // 2
    top, bottom = 75, 302
    draw.rounded_rectangle((date_x, top, right, bottom), radius=22, fill=LABEL)
    draw.rounded_rectangle((date_x, top, right, top + 136), radius=22, fill="#EFE1EA")
    draw.rectangle((date_x + 2, top + 105, right - 2, top + 130), fill="#EFE1EA")
    draw.rectangle((date_x + 2, top + 126, right - 2, top + 177), fill="#B97098")
    draw.rounded_rectangle(
        (date_x, top, right, bottom), radius=22, outline=EDGE, width=3
    )
    calendar = report["calendar"]
    _text(
        draw,
        (center, top + 66),
        calendar["weekday"][-1],
        79,
        "#B86E98",
        anchor="mm",
        weight=2,
    )
    _text(
        draw,
        (center, top + 152),
        report["day"].strftime("%Y年%m月%d日"),
        22,
        "#FFFFFF",
        anchor="mm",
        weight=1,
    )
    _text(
        draw, (center, top + 201), calendar["lunar"], 23, ACCENT, anchor="mm", weight=1
    )


def render_card(report: dict, custom_portrait: Path | None = None) -> bytes:
    """Render the portrait, calendar masthead and framed edition as a local JPEG."""
    festivals = report["calendar"]["festivals"]
    sections = report["sections"]
    rows = []
    hot = sections.get("hot")
    if festivals or hot is not None:
        calendar_w = round((CONTENT_W - GAP) * 0.40) if hot is not None else CONTENT_W
        hot_w = (CONTENT_W - GAP - calendar_w) if festivals else CONTENT_W
        calendar_h = 91 + len(festivals) * 49 + 20 if festivals else 0
        top_row = []
        if hot is not None:
            panel = _list_panel("hot", hot, hot_w, min_height=calendar_h)
            calendar_h = panel.height
        if festivals:
            top_row.append(_calendar(festivals, calendar_w, calendar_h))
        if hot is not None:
            top_row.append(panel)
        rows.append(top_row)
    if "anime" in sections:
        rows.append(
            [_anime_panel(sections["anime"], report.get("show_anime_covers", True))]
        )
    for key in ("news", "tech"):
        if key in sections:
            rows.append([_list_panel(key, sections[key], CONTENT_W)])
    for key in ("quote", "word"):
        if key in sections:
            rows.append([_feature(key, sections[key])])

    start = 319
    height = start + sum(row[0].height + GAP for row in rows) + MARGIN + 8
    canvas = Image.new("RGB", (WIDTH, height), "#E1BED2")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (MARGIN, MARGIN, WIDTH - MARGIN - 1, height - MARGIN - 1), radius=31, fill=PAPER
    )
    _masthead(canvas, report, custom_portrait)
    y = start
    for row in rows:
        x = CONTENT_X
        for panel in row:
            canvas.paste(panel, (x, y))
            x += panel.width + GAP
        y += row[0].height + GAP

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
    parts = [f"日报 · {day}"]
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
