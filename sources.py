"""Fetch the structured sources used by the daily briefing."""

import asyncio
import base64
import io
import json
import logging
import re
from collections.abc import Awaitable
from datetime import date, timedelta
from itertools import batched
from time import monotonic
from typing import Literal, NotRequired, TypedDict
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree

import aiohttp
from PIL import Image

LOGGER = logging.getLogger(__name__)

UAPI = "https://uapis.cn/api/v1"
BANGUMI_CALENDAR = "https://api.bgm.tv/calendar"
BANGUMI_EPISODES = "https://api.bgm.tv/v0/episodes"
BANGUMI_SEARCH = "https://api.bgm.tv/v0/search/subjects"
BANGUMI_NAME = "Bangumi 番组计划"
BANGUMI_EPISODE_PAGE = 100
BANGUMI_CHECK_LIMIT = 32
BANGUMI_CHECK_BATCH = 4
BANGUMI_CHECK_TIMEOUT = 10
BANGUMI_EPISODE_TTL = 45 * 60
BANGUMI_ANNUAL_TTL = 6 * 60 * 60
BILIBILI_HOTWORDS = "https://s.search.bilibili.com/main/hotword"
WORLD_NEWS = "https://60s.viki.moe/v2/60s"
ITHOME_RSS = "https://www.ithome.com/rss/"
HITOKOTO = "https://v1.hitokoto.cn/"

USER_AGENT = "Whereis-Alice/MisakiDaily/1.0.5 (+https://github.com/Whereis-Alice/astrbot_plugin_misaki_daily)"
SOURCE_TIMEOUT = aiohttp.ClientTimeout(total=7, connect=3, sock_read=4)
COVER_TIMEOUT = aiohttp.ClientTimeout(total=3, connect=2, sock_read=2)
COVER_BATCH_TIMEOUT = 5
SOURCE_LIMIT = 1024 * 1024
COVER_LIMIT = 256 * 1024
COVER_OUTPUT_LIMIT = 160 * 1024
COVER_HOSTS = frozenset({"lain.bgm.tv", "lain.bangumi.tv", "lain.chii.in"})
BILIBILI_HOSTS = frozenset({"www.bilibili.com", "search.bilibili.com"})
ITHOME_HOSTS = frozenset({"www.ithome.com", "m.ithome.com", "ithome.com"})
NEWS_HOSTS = frozenset({"mp.weixin.qq.com", "60s.viki.moe"})
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
TEXT_CONTROLS = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ud800-\udfff\ufeff]"
)
COVER_PATH = re.compile(
    r"(?:/r/(?:100|200|400|800))?/pic/cover/[lmcsg]/[a-zA-Z0-9/_-]+\.(?:jpe?g|png|webp)",
    re.IGNORECASE,
)
NUMBERED_PREFIX = re.compile(r"^\d+[.、]\s*")


class Headline(TypedDict):
    title: str
    url: str
    cover: str
    meta: str
    phonetic: NotRequired[str]


class Section(TypedDict):
    status: Literal["ok", "empty", "error"]
    source: str
    items: list[Headline]


class SourceResponseError(ValueError):
    """A response unusable for the requested source."""


SOURCE_ERRORS = (
    aiohttp.ClientError,
    TimeoutError,
    ValueError,
    ElementTree.ParseError,
    RecursionError,
)


def _text(value: object, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    value = value.encode("utf-8", errors="replace").decode("utf-8")
    clean = " ".join(TEXT_CONTROLS.sub(" ", value).split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _public_link(value: object, allowed_hosts: frozenset[str]) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        return ""
    if (
        not value.isascii()
        or "\\" in value
        or any(not 32 < ord(char) < 127 for char in value)
    ):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if (
        parts.scheme not in {"http", "https"}
        or parts.netloc.lower() not in allowed_hosts
    ):
        return ""
    return urlunsplit(
        ("https", parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )


def _proxy_url(value: str) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0:
            return value
    except ValueError:
        pass
    LOGGER.warning("美咲日报忽略了无效的 Bangumi HTTP(S) 代理")
    return ""


def _cover_link(value: object) -> str:
    link = _public_link(value, COVER_HOSTS)
    if not link:
        return ""
    parts = urlsplit(link)
    if parts.query or parts.fragment or not COVER_PATH.fullmatch(parts.path):
        return ""
    return link


def _cover_candidates(images: dict) -> tuple[str, ...]:
    candidates = []
    # The legacy calendar exposes cropped thumbnails. Resize its original on the CDN
    # using the same 400-pixel endpoint as the current subject API.
    original = _cover_link(images.get("large"))
    if original:
        parts = urlsplit(original)
        if parts.path.startswith("/pic/cover/l/"):
            candidates.append(parts._replace(path=f"/r/400{parts.path}").geturl())
        else:
            candidates.append(original)
    for key in ("common", "medium", "small"):
        link = _cover_link(images.get(key))
        if link:
            candidates.append(link)
    if original:
        candidates.append(original)
    return tuple(dict.fromkeys(candidates))


def _air_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value: object) -> list:
    if not isinstance(value, list):
        raise SourceResponseError("invalid list")
    return value


def _headline(
    title: str, *, url: str = "", cover: str = "", meta: str = ""
) -> Headline:
    return {"title": title, "url": url, "cover": cover, "meta": meta}


def _section(source: str, items: list[Headline]) -> Section:
    return {"status": "ok" if items else "empty", "source": source, "items": items}


def _error(source: str, error: Exception) -> Section:
    # Request exception strings can contain proxy credentials or unsafe upstream text.
    detail = (
        str(error) if isinstance(error, SourceResponseError) else type(error).__name__
    )
    LOGGER.warning("美咲日报数据源 %s 请求失败（%s）", source, detail)
    return {"status": "error", "source": source, "items": []}


def _encode_cover(blob: bytes) -> str:
    with Image.open(io.BytesIO(blob), formats=("JPEG", "PNG", "WEBP")) as image:
        if image.width * image.height > 4_000_000:
            return ""
        image.thumbnail((400, 720), Image.Resampling.LANCZOS)
        rgb = image.convert("RGB")
        for quality in (91, 82):
            with io.BytesIO() as output:
                rgb.save(output, "JPEG", quality=quality, optimize=True, subsampling=0)
                if output.tell() <= COVER_OUTPUT_LIMIT:
                    encoded = base64.b64encode(output.getvalue()).decode("ascii")
                    return "data:image/jpeg;base64," + encoded
    return ""


class DailySources:
    """Fetch independent briefing sections and keep partial upstream failures local."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        bangumi_proxy: str = "",
        show_covers: bool = False,
        uapi_token: str = "",
    ) -> None:
        self.session = session
        self.bangumi_proxy = _proxy_url(bangumi_proxy.strip())
        self.show_covers = show_covers
        self._uapi_token = uapi_token.strip()
        self._request_slots = asyncio.Semaphore(6)
        self._cover_slots = asyncio.Semaphore(3)
        self._episode_cache: dict[int, tuple[float, frozenset[date]]] = {}
        self._annual_cache: tuple[date, float, list] | None = None

    async def collect(
        self,
        day: date,
        *,
        anime_count: int,
        hot_count: int,
        news_count: int,
        tech_count: int,
        show_quote: bool,
        show_word: bool,
    ) -> dict[str, Section]:
        jobs: dict[str, Awaitable[Section]] = {}
        if anime_count > 0:
            jobs["anime"] = self._anime(day, min(anime_count, 12))
        if hot_count > 0:
            jobs["hot"] = self._hot(min(hot_count, 12))
        if news_count > 0:
            jobs["news"] = self._news(day, min(news_count, 12))
        if tech_count > 0:
            jobs["tech"] = self._tech(min(tech_count, 12))
        if show_quote:
            jobs["quote"] = self._quote()
        if show_word:
            jobs["word"] = self._word(day)
        sections = await asyncio.gather(*jobs.values())
        return dict(zip(jobs, sections, strict=True))

    async def _read(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        proxy: str | None = None,
        as_cover: bool = False,
        referer: str = "",
        json_body: dict | None = None,
    ) -> bytes:
        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer
        if self._uapi_token and url.startswith(f"{UAPI}/"):
            headers["Authorization"] = f"Bearer {self._uapi_token}"
        limit = COVER_LIMIT if as_cover else SOURCE_LIMIT
        timeout = COVER_TIMEOUT if as_cover else SOURCE_TIMEOUT

        request_args = {
            "params": params,
            "proxy": proxy,
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": False,
        }
        request = self.session.get
        if json_body is not None:
            request = self.session.post
            request_args["json"] = json_body
        async with self._request_slots, request(url, **request_args) as response:
            if response.status != 200:
                raise SourceResponseError(f"HTTP {response.status}")
            if as_cover and response.content_type not in IMAGE_TYPES:
                raise SourceResponseError("unsupported cover type")
            if response.content_length is not None and response.content_length > limit:
                raise SourceResponseError("response too large")
            buffer = bytearray()
            async for chunk in response.content.iter_chunked(32 * 1024):
                if len(buffer) + len(chunk) > limit:
                    raise SourceResponseError("response too large")
                buffer.extend(chunk)
            return bytes(buffer)

    async def _json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        proxy: str | None = None,
        referer: str = "",
        json_body: dict | None = None,
    ) -> object:
        body = await self._read(
            url, params=params, proxy=proxy, referer=referer, json_body=json_body
        )
        return json.loads(body)

    def _bangumi_routes(self, proxy: str | None = None) -> tuple[str | None, ...]:
        if proxy is None and self.bangumi_proxy:
            return None, self.bangumi_proxy
        return (proxy,)

    async def _bangumi_json(
        self,
        url: str,
        *,
        proxy: str | None,
        params: dict[str, str],
        json_body: dict | None = None,
    ) -> object:
        try:
            return await self._json(
                url, params=params, proxy=proxy, json_body=json_body
            )
        except SOURCE_ERRORS:
            if proxy is not None or not self.bangumi_proxy:
                raise
        return await self._json(
            url, params=params, proxy=self.bangumi_proxy, json_body=json_body
        )

    async def _download_cover(self, urls: tuple[str, ...], proxy: str | None) -> str:
        if not urls:
            return ""
        async with self._cover_slots:
            for url in urls:
                for route in self._bangumi_routes(proxy):
                    try:
                        blob = await self._read(url, as_cover=True, proxy=route)
                        cover = await asyncio.to_thread(_encode_cover, blob)
                    except (*SOURCE_ERRORS, OSError, Image.DecompressionBombError):
                        continue
                    if cover:
                        return cover
                    break
        return ""

    async def _anime(self, day: date, count: int) -> Section:
        for proxy in self._bangumi_routes():
            try:
                return await self._anime_from_calendar(day, count, proxy)
            except SOURCE_ERRORS as error:
                failed = _error(BANGUMI_NAME, error)
        return failed

    async def _annual_subjects(self, day: date, proxy: str | None) -> list:
        cached = self._annual_cache
        if cached and cached[0] == day and monotonic() < cached[1]:
            return cached[2]
        try:
            data = _object(
                await self._bangumi_json(
                    BANGUMI_SEARCH,
                    params={"limit": "20"},
                    proxy=proxy,
                    json_body={
                        "keyword": "",
                        "sort": "heat",
                        "filter": {
                            "type": [2],
                            "tag": ["年番"],
                            "air_date": [f">={day - timedelta(days=550)}", f"<={day}"],
                        },
                    },
                )
            )
            subjects = _array(data.get("data"))
        except SOURCE_ERRORS as error:
            _error("Bangumi 年番补充检索", error)
            return []
        self._annual_cache = day, monotonic() + BANGUMI_ANNUAL_TTL, subjects
        return subjects

    async def _episode_dates(
        self, subject_id: int, proxy: str | None
    ) -> frozenset[date]:
        cached = self._episode_cache.get(subject_id)
        if cached and monotonic() < cached[0]:
            return cached[1]

        params = {
            "subject_id": str(subject_id),
            "type": "0",
            "limit": str(BANGUMI_EPISODE_PAGE),
        }
        response = _object(
            await self._bangumi_json(BANGUMI_EPISODES, params=params, proxy=proxy)
        )
        episodes = _array(response.get("data"))
        total = response.get("total")
        if type(total) is int and total > len(episodes) and total <= 100_000:
            # Check the newest page of very long titles without scanning their full history.
            offset = str(max(0, total - BANGUMI_EPISODE_PAGE))
            tail = _object(
                await self._bangumi_json(
                    BANGUMI_EPISODES, params={**params, "offset": offset}, proxy=proxy
                )
            )
            episodes += _array(tail.get("data"))

        dates = frozenset(
            airdate
            for episode in map(_object, episodes)
            if episode.get("type") == 0
            and (airdate := _air_date(episode.get("airdate"))) is not None
        )
        if len(self._episode_cache) >= 200:
            self._episode_cache.pop(next(iter(self._episode_cache)))
        self._episode_cache[subject_id] = monotonic() + BANGUMI_EPISODE_TTL, dates
        return dates

    async def _anime_item(
        self,
        entry: dict,
        day: date,
        proxy: str | None,
    ) -> tuple[Headline, tuple[str, ...]] | None:
        title = _text(entry.get("name_cn"), 90) or _text(entry.get("name"), 90)
        if not title:
            return None
        subject_id = entry.get("id")
        premiere = _air_date(entry.get("air_date")) or _air_date(entry.get("date"))
        try:
            dates = await self._episode_dates(subject_id, proxy)
        except SOURCE_ERRORS:
            if premiere != day:
                raise
            dates = frozenset()
        if day not in dates and (dates or premiere != day):
            return None

        covers = (
            _cover_candidates(_object(entry.get("images"))) if self.show_covers else ()
        )
        return _headline(title, url=f"https://bgm.tv/subject/{subject_id}"), covers

    async def _anime_from_calendar(
        self,
        day: date,
        count: int,
        proxy: str | None,
    ) -> Section:
        calendar = _array(await self._json(BANGUMI_CALENDAR, proxy=proxy))
        weekly = next(
            (
                _array(entry.get("items"))
                for entry in map(_object, calendar)
                if _object(entry.get("weekday")).get("id") == day.isoweekday()
            ),
            [],
        )
        annual = list(map(_object, await self._annual_subjects(day, proxy)))
        regular_weekday = [
            entry
            for entry in annual
            if (premiere := _air_date(entry.get("date")))
            and premiere.isoweekday() == day.isoweekday()
        ]

        candidates = {}
        for entry in map(_object, [*regular_weekday, *weekly, *annual]):
            subject_id = entry.get("id")
            if type(subject_id) is not int or not 0 < subject_id < 2**64:
                continue
            premiere = _air_date(entry.get("air_date")) or _air_date(entry.get("date"))
            if premiere is None or premiere <= day:
                candidates.setdefault(subject_id, entry)

        matches: list[tuple[Headline, tuple[str, ...]]] = []
        incomplete = False
        try:
            async with asyncio.timeout(BANGUMI_CHECK_TIMEOUT):
                for batch in batched(
                    list(candidates.values())[:BANGUMI_CHECK_LIMIT], BANGUMI_CHECK_BATCH
                ):
                    results = await asyncio.gather(
                        *(self._anime_item(entry, day, proxy) for entry in batch),
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, SOURCE_ERRORS):
                            incomplete = True
                        elif isinstance(result, BaseException):
                            raise result
                        elif result is not None:
                            matches.append(result)
                    if len(matches) >= count:
                        break
        except TimeoutError:
            incomplete = True

        if incomplete and not matches:
            return _error(BANGUMI_NAME, SourceResponseError("分集日期暂时无法核对"))
        matches = matches[:count]
        if self.show_covers and any(cover for _, cover in matches):

            async def populate_cover(item: Headline, urls: tuple[str, ...]) -> None:
                item["cover"] = await self._download_cover(urls, proxy)

            try:
                async with asyncio.timeout(COVER_BATCH_TIMEOUT):
                    await asyncio.gather(
                        *(populate_cover(item, urls) for item, urls in matches)
                    )
            except TimeoutError:
                LOGGER.debug("美咲日报番剧封面下载已到时限，保留已取得的封面")
        return _section(BANGUMI_NAME, [item for item, _ in matches])

    async def _uapi_hotboard(
        self, type_name: str, count: int, hosts: frozenset[str]
    ) -> list[Headline]:
        data = _object(
            await self._json(f"{UAPI}/misc/hotboard", params={"type": type_name})
        )
        items: list[Headline] = []
        for entry in map(_object, _array(data.get("list"))):
            title = _text(entry.get("title"))
            if not title:
                continue
            url = _public_link(entry.get("url"), hosts)
            items.append(
                _headline(title, url=url, meta=_text(entry.get("hot_value"), 32))
            )
            if len(items) >= count:
                break
        return items

    async def _hot(self, count: int) -> Section:
        source = "UAPI · 哔哩哔哩热门"
        try:
            return _section(
                source, await self._uapi_hotboard("bilibili", count, BILIBILI_HOSTS)
            )
        except SOURCE_ERRORS as error:
            _error(source, error)

        source = "哔哩哔哩热搜"
        try:
            data = _object(
                await self._json(BILIBILI_HOTWORDS, referer="https://www.bilibili.com/")
            )
            if data.get("code") != 0:
                raise SourceResponseError("unexpected Bilibili response")
            items: list[Headline] = []
            for entry in map(_object, _array(data.get("list"))):
                title = _text(entry.get("show_name")) or _text(entry.get("keyword"))
                if not title:
                    continue
                link = (
                    f"https://search.bilibili.com/all?keyword={quote(title, safe='')}"
                )
                items.append(_headline(title, url=link))
                if len(items) >= count:
                    break
            return _section(source, items)
        except SOURCE_ERRORS as error:
            return _error(source, error)

    async def _news(self, day: date, count: int) -> Section:
        source = "60s API"
        try:
            response = _object(await self._json(WORLD_NEWS))
            if response.get("code") != 200:
                raise SourceResponseError("unexpected 60s API response")
            data = _object(response.get("data"))
            issue_date = _text(data.get("date"), 10)
            if not issue_date:
                raise SourceResponseError("missing news date")
            if date.fromisoformat(issue_date) != day:
                return _section(source, [])
            url = _public_link(data.get("link"), NEWS_HOSTS)
            items: list[Headline] = []
            for entry in _array(data.get("news")):
                title = NUMBERED_PREFIX.sub("", _text(entry), count=1)
                if title:
                    items.append(_headline(title, url=url))
                if len(items) >= count:
                    break
            return _section(source, items)
        except SOURCE_ERRORS as error:
            return _error(source, error)

    async def _tech(self, count: int) -> Section:
        source = "IT之家 RSS"
        try:
            raw = await self._read(ITHOME_RSS)
            # Work with plain UTF-8 to reject DTDs before the XML parser expands them.
            xml = raw.decode("utf-8-sig")
            if "<!DOCTYPE" in xml.upper():
                raise SourceResponseError("unexpected XML document type")
            root = ElementTree.fromstring(xml)
            channel = root.find("channel")
            if root.tag != "rss" or channel is None:
                raise SourceResponseError("invalid RSS document")
            items: list[Headline] = []
            for entry in channel.iterfind("item"):
                title = _text(entry.findtext("title"))
                if not title:
                    continue
                items.append(
                    _headline(
                        title, url=_public_link(entry.findtext("link"), ITHOME_HOSTS)
                    )
                )
                if len(items) >= count:
                    break
            return _section(source, items)
        except SOURCE_ERRORS as error:
            _error(source, error)

        source = "UAPI · IT之家热榜"
        try:
            return _section(
                source, await self._uapi_hotboard("ithome", count, ITHOME_HOSTS)
            )
        except SOURCE_ERRORS as error:
            return _error(source, error)

    async def _quote(self) -> Section:
        source = "UAPI 一言"
        try:
            data = _object(await self._json(f"{UAPI}/saying"))
            title = _text(data.get("text"))
            if not title:
                raise SourceResponseError("missing quote")
            return _section(source, [_headline(title)])
        except SOURCE_ERRORS as error:
            _error(source, error)

        source = "Hitokoto 一言"
        try:
            data = _object(await self._json(HITOKOTO))
            title = _text(data.get("hitokoto"))
            if not title:
                raise SourceResponseError("missing quote")
            origin = _text(data.get("from"), 52)
            author = _text(data.get("from_who"), 40)
            attribution = " · ".join(
                dict.fromkeys(part for part in (origin, author) if part)
            )
            return _section(source, [_headline(title, meta=attribution)])
        except SOURCE_ERRORS as error:
            return _error(source, error)

    async def _word(self, day: date) -> Section:
        source = "UAPI 每日单词"
        try:
            data = _object(
                await self._json(
                    f"{UAPI}/daily/word",
                    params={"date": day.isoformat(), "count": "1", "example": "false"},
                )
            )
            items: list[Headline] = []
            for entry in map(_object, _array(data.get("words"))):
                title = _text(entry.get("word"), 48)
                if not title:
                    continue
                phonetic = _text(entry.get("phonetic"), 40)
                meaning = _text(entry.get("translation"), 100) or _text(
                    entry.get("definition"), 100
                )
                item = _headline(title, meta=meaning)
                if phonetic:
                    item["phonetic"] = phonetic
                items.append(item)
                break
            return _section(source, items)
        except SOURCE_ERRORS as error:
            return _error(source, error)
