from __future__ import annotations

import base64
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ASSETS = Path(__file__).resolve().parent / "assets"
WIDTH = 960
RENDER_SCALE = 2
MAX_IMAGE_HEIGHT = 8000
MARGIN = 27
CONTENT_X = 54
CONTENT_W = WIDTH - 2 * CONTENT_X
GAP = 18
INK = "#263C2F"
MUTED = "#526D5D"
ACCENT = "#356A4D"
EDGE = "#90BC9D"
PAPER = "#F1F8F0"
PANEL = "#F8FCF6"
LABEL = "#FDFFF9"
TINT = "#E3F1E3"
RULE = "#D7E8D8"
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


def _pixels(*values: int) -> tuple[int, ...]:
    return tuple(value * RENDER_SCALE for value in values)


@lru_cache(maxsize=36)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSansSC-Regular.otf"), size)


@lru_cache(maxsize=1)
def _masthead_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "DailyMasthead.ttf"), 164 * RENDER_SCALE)


@lru_cache(maxsize=1)
def _phonetic_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "NotoSans-Latin.ttf"), 23 * RENDER_SCALE)


@lru_cache(maxsize=len(ICONS))
def _section_icon(key: str) -> Image.Image:
    with Image.open(ASSETS / "icons" / ICONS[key]) as icon:
        return ImageOps.contain(
            icon.convert("RGBA"), _pixels(31, 31), Image.Resampling.LANCZOS
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
        _pixels(*xy),
        value,
        font=_font(size * RENDER_SCALE),
        fill=color,
        anchor=anchor,
        stroke_width=weight * RENDER_SCALE,
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
    image = Image.new("RGB", _pixels(width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        _pixels(1, 28, width - 2, height - 2),
        radius=18 * RENDER_SCALE,
        fill=PANEL,
        outline=EDGE,
        width=4 * RENDER_SCALE,
    )
    label = LABELS[key]
    label_right = 83 + round(_font(27).getlength(label)) + 16
    draw.rounded_rectangle(
        _pixels(19, 1, label_right, 55),
        radius=15 * RENDER_SCALE,
        fill=LABEL,
        outline=EDGE,
        width=3 * RENDER_SCALE,
    )
    if key in ICONS:
        icon = _section_icon(key)
        image.paste(
            icon,
            (
                31 * RENDER_SCALE + (31 * RENDER_SCALE - icon.width) // 2,
                12 * RENDER_SCALE + (31 * RENDER_SCALE - icon.height) // 2,
            ),
            icon,
        )
    else:
        draw.rounded_rectangle(
            _pixels(31, 12, 62, 43), radius=7 * RENDER_SCALE, fill=TINT
        )
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
            draw.line(
                _pixels(24, y + 34, width - 24, y + 34), fill=RULE, width=RENDER_SCALE
            )
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
            draw.rounded_rectangle(
                _pixels(24, y + 1, 54, y + 31), radius=7 * RENDER_SCALE, fill=color
            )
            _text(draw, (39, y + 16), label, 19, "#FFFFFF", anchor="mm")
        else:
            draw.ellipse(_pixels(27, y + 11, 34, y + 18), fill=ACCENT)
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
    poster_size = _pixels(poster_w, poster_h)
    mask = Image.new("L", poster_size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, poster_size[0] - 1, poster_size[1] - 1),
        radius=11 * RENDER_SCALE,
        fill=255,
    )
    for index, item in enumerate(items):
        x = 24 + (index % columns) * (poster_w + gap)
        y = 78 + (index // columns) * row_height
        poster = _cover(item.get("cover", ""), poster_size)
        if poster is None:
            draw.rounded_rectangle(
                _pixels(x, y, x + poster_w - 1, y + poster_h - 1),
                radius=11 * RENDER_SCALE,
                fill=TINT,
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
            image.paste(poster, _pixels(x, y), mask)
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
        draw.text(
            _pixels(26, y), phonetic, font=_phonetic_font(), fill=MUTED, anchor="lt"
        )
        y += 31
    for index, line in enumerate(meta):
        xy = (CONTENT_W - 30, y) if quote else (26, y)
        prefix = "—— " if quote and index == 0 else ""
        _text(draw, xy, prefix + line, 22, MUTED, anchor="rt" if quote else "lt")
        y += 31
    return image


def _masthead(canvas: Image.Image, report: dict, portrait: Image.Image | None) -> None:
    draw = ImageDraw.Draw(canvas)
    for y in range(59, 307, 9):
        for x in range(57 + (y % 2) * 4, WIDTH - 59, 9):
            px, py = _pixels(x, y)
            draw.rectangle(
                (px, py, px + RENDER_SCALE - 1, py + RENDER_SCALE - 1), fill=RULE
            )

    if portrait is not None:
        portrait = portrait.copy()
        portrait.thumbnail(_pixels(210, 215), Image.Resampling.LANCZOS)
        canvas.paste(
            portrait,
            (
                170 * RENDER_SCALE - portrait.width // 2,
                272 * RENDER_SCALE - portrait.height,
            ),
            portrait,
        )
    else:
        draw.ellipse(
            _pixels(94, 91, 239, 236), outline="#B4D5BC", width=2 * RENDER_SCALE
        )
        draw.line(
            [
                _pixels(98, 201),
                _pixels(225, 122),
                _pixels(192, 223),
                _pixels(164, 194),
                _pixels(139, 216),
                _pixels(149, 183),
                _pixels(98, 201),
            ],
            fill="#70A784",
            width=4 * RENDER_SCALE,
            joint="curve",
        )
        draw.line(
            [_pixels(149, 183), _pixels(225, 122), _pixels(164, 194)],
            fill="#70A784",
            width=3 * RENDER_SCALE,
            joint="curve",
        )

    title_font = _masthead_font()
    for x, char in zip((380, 556), "日报", strict=True):
        draw.text(_pixels(x, 86), char, font=title_font, fill=ACCENT, anchor="mt")

    date_x = WIDTH - CONTENT_X - 205
    right = WIDTH - CONTENT_X
    center = (date_x + right) // 2
    top, bottom = 75, 302
    draw.rounded_rectangle(
        _pixels(date_x, top, right, bottom), radius=22 * RENDER_SCALE, fill=LABEL
    )
    draw.rounded_rectangle(
        _pixels(date_x, top, right, top + 136), radius=22 * RENDER_SCALE, fill=TINT
    )
    draw.rectangle(_pixels(date_x + 2, top + 105, right - 2, top + 130), fill=TINT)
    draw.rectangle(_pixels(date_x + 2, top + 126, right - 2, top + 177), fill="#3D7958")
    draw.rounded_rectangle(
        _pixels(date_x, top, right, bottom),
        radius=22 * RENDER_SCALE,
        outline=EDGE,
        width=3 * RENDER_SCALE,
    )
    calendar = report["calendar"]
    _text(
        draw,
        (center, top + 66),
        calendar["weekday"][-1],
        79,
        ACCENT,
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


def render_card(report: dict, portrait: Image.Image | None = None) -> bytes:
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
            calendar_h = panel.height // RENDER_SCALE
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

    start = 319 * RENDER_SCALE
    gap = GAP * RENDER_SCALE
    height = (
        start + sum(row[0].height + gap for row in rows) + (MARGIN + 8) * RENDER_SCALE
    )
    canvas = Image.new("RGB", (WIDTH * RENDER_SCALE, height), "#B9D8C0")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (
            *_pixels(MARGIN, MARGIN),
            canvas.width - MARGIN * RENDER_SCALE - 1,
            height - MARGIN * RENDER_SCALE - 1,
        ),
        radius=31 * RENDER_SCALE,
        fill=PAPER,
    )
    _masthead(canvas, report, portrait)
    y = start
    for row in rows:
        x = CONTENT_X * RENDER_SCALE
        for panel in row:
            canvas.paste(panel, (x, y))
            x += panel.width + gap
        y += row[0].height + gap

    if height > MAX_IMAGE_HEIGHT:
        # Telegram photos require width plus height to stay within 10,000 pixels.
        canvas.thumbnail((canvas.width, MAX_IMAGE_HEIGHT), Image.Resampling.LANCZOS)

    with BytesIO() as buffer:
        canvas.save(
            buffer,
            format="JPEG",
            quality=95,
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
