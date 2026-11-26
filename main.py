from __future__ import annotations

import asyncio
import base64
import re
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .calendar_data import build_calendar
from .card import render_card, render_text_pages
from .schedule import group_origins, next_run
from .sources import DailySources

_DEFAULT_ZONE = "Asia/Shanghai"
_PUSH_STATE_KEY = "scheduled_delivery"
_DELIVERY_WINDOW = timedelta(minutes=10)


class MisakiDailyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        zone_name = str(config.get("timezone", _DEFAULT_ZONE)).strip()
        try:
            self.zone = ZoneInfo(zone_name)
        except (ValueError, ZoneInfoNotFoundError):
            logger.warning("[美咲日报] 无效时区 %r，使用 %s", zone_name, _DEFAULT_ZONE)
            self.zone = ZoneInfo(_DEFAULT_ZONE)

        self._session: aiohttp.ClientSession | None = None
        self._sources: DailySources | None = None
        self._push_task: asyncio.Task[None] | None = None
        self._report_lock = asyncio.Lock()
        self._image_lock = asyncio.Lock()
        self._report_cache: tuple[date, float, dict] | None = None
        self._image_cache: tuple[dict, str] | None = None
        self._portrait_path = None

    async def initialize(self) -> None:
        if self._session is not None:
            return
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12),
            connector=aiohttp.TCPConnector(limit=12, limit_per_host=4),
            trust_env=True,
        )
        self._sources = DailySources(
            self._session,
            bangumi_proxy=str(self.config.get("bangumi_proxy", "")).strip(),
            show_covers=bool(self.config.get("show_anime_covers", True)),
        )
        try:
            self._portrait_path = (
                StarTools.get_data_dir("astrbot_plugin_misaki_daily") / "portrait.webp"
            )
        except (OSError, RuntimeError):
            logger.warning("[美咲日报] 无法读取自定义立绘目录")

        if self.config.get("enable_scheduled_push", False):
            value = str(self.config.get("scheduled_push_time", "08:00")).strip()
            if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
                logger.warning("[美咲日报] 定时推送未启动，时间须为 24 小时制 HH:MM")
                return
            entries = self.config.get("scheduled_push_groups", [])
            groups = group_origins(entries if isinstance(entries, list) else [])
            if not groups:
                logger.warning("[美咲日报] 定时推送未启动，未配置有效的群聊会话标识")
                return
            if len(groups) != len(entries):
                logger.warning("[美咲日报] 已跳过无效或重复的定时推送会话标识")
            self._push_task = asyncio.create_task(
                self._run_schedule(time.fromisoformat(value), groups),
                name="misaki-daily-push",
            )

    async def terminate(self) -> None:
        if self._push_task is not None:
            self._push_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._push_task
            self._push_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._sources = None
        self._report_cache = self._image_cache = None

    @filter.command("美咲日报")
    async def misaki_daily(self, event: AstrMessageEvent):
        """查看今天的美咲日报。"""
        report = await self._get_report(datetime.now(self.zone).date())
        image = await self._get_image(report)
        if image:
            yield event.make_result().base64_image(image)
        else:
            for page in render_text_pages(report):
                yield event.plain_result(page).use_t2i(False)

    @filter.command("美咲日报文字")
    async def misaki_daily_text(self, event: AstrMessageEvent):
        """查看适合低带宽及文字阅读的日报。"""
        report = await self._get_report(datetime.now(self.zone).date())
        for page in render_text_pages(report):
            yield event.plain_result(page).use_t2i(False)

    @filter.command("美咲日报来源")
    async def misaki_daily_links(self, event: AstrMessageEvent):
        """获取本期栏目可用的原文链接。"""
        report = await self._get_report(datetime.now(self.zone).date())
        for page in render_text_pages(report, links_only=True):
            yield event.plain_result(page).use_t2i(False)

    @filter.command("美咲日报会话")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def misaki_daily_session(self, event: AstrMessageEvent):
        """查看定时推送所需的当前群聊会话标识。"""
        origin = event.unified_msg_origin
        if group_origins([origin]):
            yield event.plain_result(origin).use_t2i(False)
        else:
            yield event.plain_result("请在接收推送的群聊中使用此命令。")

    def _number(self, key: str, default: int, upper: int, lower: int = 0) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError, OverflowError):
            value = default
        return min(upper, max(lower, value))

    async def _get_report(self, day: date) -> dict:
        async with self._report_lock:
            cached = self._report_cache
            if cached and cached[0] == day and monotonic() < cached[1]:
                return cached[2]
            assert self._sources is not None
            sections = await self._sources.collect(
                day,
                anime_count=self._number("max_anime_count", 4, 8),
                hot_count=self._number("max_hotword_count", 4, 8),
                news_count=self._number("max_news_count", 5, 8),
                tech_count=self._number("max_tech_count", 4, 8),
                show_quote=bool(self.config.get("show_quote", True)),
                show_word=bool(self.config.get("show_word", False)),
            )
            report = {
                "day": day,
                "show_anime_covers": bool(self.config.get("show_anime_covers", True)),
                "calendar": build_calendar(
                    day, self._number("max_holiday_count", 3, 8)
                ),
                "sections": sections,
            }
            cache_seconds = 60 * self._number("cache_minutes", 20, 120, 5)
            if any(section["status"] == "error" for section in sections.values()):
                cache_seconds = min(cache_seconds, 300)
            self._report_cache = day, monotonic() + cache_seconds, report
            return report

    async def _get_image(self, report: dict) -> str:
        async with self._image_lock:
            if self._image_cache and self._image_cache[0] is report:
                return self._image_cache[1]
            try:
                content = await asyncio.to_thread(
                    render_card, report, self._portrait_path
                )
                image = base64.b64encode(content).decode("ascii")
            except (OSError, ValueError, RuntimeError):
                logger.exception("[美咲日报] 图片生成失败，改用文字日报")
                image = ""
            self._image_cache = report, image
            return image

    async def _run_schedule(self, at: time, groups: tuple[str, ...]) -> None:
        run_at = next_run(datetime.now(self.zone), at)
        while True:
            now = datetime.now(self.zone)
            delay = (
                run_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)
            ).total_seconds()
            if delay > 0:
                await asyncio.sleep(min(delay, 60))
                continue

            if -delay <= _DELIVERY_WINDOW.total_seconds():
                try:
                    await self._push(run_at.date(), groups)
                except Exception:  # noqa: BLE001 - A failed run must leave the scheduler active.
                    logger.exception("[美咲日报] 定时推送运行失败")
            now = datetime.now(self.zone)
            # Preserve the attempted day if a clock correction moves time backwards.
            last_instant = max(
                now.astimezone(timezone.utc), run_at.astimezone(timezone.utc)
            )
            run_at = next_run(last_instant.astimezone(self.zone), at)

    async def _push(self, day: date, groups: tuple[str, ...]) -> None:
        today = day.isoformat()
        state = await self.get_kv_data(_PUSH_STATE_KEY, {})
        previous = (
            state.get("targets")
            if isinstance(state, dict) and state.get("date") == today
            else None
        )
        if not isinstance(previous, list):
            previous = []
        attempted = dict.fromkeys(
            origin for origin in previous if isinstance(origin, str)
        )
        remaining = [origin for origin in groups if origin not in attempted]
        if not remaining:
            return

        report = await self._get_report(day)
        use_image = self.config.get("push_format", "图片") != "文字"
        image = await self._get_image(report) if use_image else ""
        pages = [] if image else render_text_pages(report)

        for origin in remaining:
            first = MessageChain()
            if self.config.get("enable_ai_greeting", False):
                first.message(await self._greeting(origin, report) + "\n")
            if image:
                chains = [first.base64_image(image)]
            else:
                chains = [
                    first.message(pages[0]),
                    *(MessageChain().message(page) for page in pages[1:]),
                ]

            # Some adapters can report a timeout after accepting the message.
            attempted[origin] = None
            await self.put_kv_data(
                _PUSH_STATE_KEY, {"date": today, "targets": list(attempted)}
            )
            try:
                for chain in chains:
                    sent = await asyncio.wait_for(
                        self.context.send_message(origin, chain), timeout=60
                    )
                    if not sent:
                        logger.warning(
                            "[美咲日报] 推送平台未找到，检查目标会话是否属于已启用的平台"
                        )
                        break
            except Exception as exc:  # noqa: BLE001 - Adapters can raise their own exception types.
                logger.warning("[美咲日报] 推送失败（%s）", type(exc).__name__)

    async def _greeting(self, origin: str, report: dict) -> str:
        day = report["day"].isoformat()
        upcoming = report["calendar"]["festivals"]
        festival = next(
            (item["name"] for item in upcoming if item["days_left"] == 0), "无"
        )
        try:
            async with asyncio.timeout(8):
                provider = str(self.config.get("greeting_provider_id", "")).strip()
                if not provider:
                    provider = await self.context.get_current_chat_provider_id(origin)
                if provider:
                    result = await self.context.llm_generate(
                        chat_provider_id=provider,
                        prompt=f"今天是 {day}，今日节日或周末提示为：{festival}。"
                        "为中文群聊的《美咲日报》写一句友好的简短招呼，50 字以内。仅输出这句话，不加引号。",
                    )
                    greeting = " ".join((result.completion_text or "").split()).strip(
                        "\"'“”"
                    )
                    if 0 < len(greeting) <= 50:
                        return greeting.replace("@", "＠")
        except Exception as exc:  # noqa: BLE001 - Providers can raise their own exception types.
            logger.debug("[美咲日报] AI 问候降级（%s）", type(exc).__name__)
        return "今天的美咲日报送到了，请查收。"
