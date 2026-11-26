"""Resolve a local portrait or cache the portrait published by sprite."""

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

import aiohttp
from PIL import Image

ASSETS = Path(__file__).resolve().parent / "assets"
OFFICIAL_PORTRAIT_URL = (
    "https://framerusercontent.com/images/d1XMAcN3f16oO52zqizH1IxYW4.webp"
)
_OFFICIAL_SHA256 = "faff1fa1d34ae2a64d8d60e11245010e214cc8c8c42bc76ebcf814844bc4478c"
_OFFICIAL_MAX_BYTES = 512 * 1024
_OFFICIAL_CACHE_NAME = "sprite-misaki-uniform.webp"
_LOGGER = logging.getLogger(__name__)


def prepare_portrait(
    source: Path | BytesIO, *, crop_height: int | None = None
) -> Image.Image:
    """Load and size an image for the masthead while preserving transparency."""
    with Image.open(source, formats=("WEBP", "PNG", "JPEG")) as image:
        if image.width * image.height > 12_000_000:
            raise ValueError("portrait too large")
        portrait = image.convert("RGBA")
    if crop_height is not None:
        portrait = portrait.crop(
            (0, 0, portrait.width, min(crop_height, portrait.height))
        )
    bounds = portrait.getbbox(alpha_only=True)
    if bounds is None:
        raise ValueError("transparent portrait")
    portrait = portrait.crop(bounds)
    portrait.thumbnail((210, 215), Image.Resampling.LANCZOS)
    return portrait


def _local_portrait(path: Path) -> Image.Image | None:
    try:
        if path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
            return prepare_portrait(path)
    except (OSError, ValueError, Image.DecompressionBombError):
        _LOGGER.warning("[美咲日报] 本地立绘读取失败")
    return None


def _official_portrait(data: bytes) -> Image.Image:
    if hashlib.sha256(data).hexdigest() != _OFFICIAL_SHA256:
        raise ValueError("official portrait checksum mismatch")
    return prepare_portrait(BytesIO(data), crop_height=590)


def _cached_portrait(path: Path) -> Image.Image | None:
    try:
        with path.open("rb") as cache:
            return _official_portrait(cache.read(_OFFICIAL_MAX_BYTES + 1))
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _save_cache(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write(data)
        temporary.replace(path)
    except OSError:
        _LOGGER.debug("[美咲日报] 立绘缓存写入失败")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _LOGGER.debug("[美咲日报] 立绘缓存临时文件清理失败")


async def load_portrait(
    session: aiohttp.ClientSession, data_dir: Path | None
) -> Image.Image | None:
    """Prefer local files, then the cached or hosted official portrait."""
    if data_dir is not None:
        portrait = await asyncio.to_thread(_local_portrait, data_dir / "portrait.webp")
        if portrait is not None:
            return portrait
    portrait = await asyncio.to_thread(_local_portrait, ASSETS / "portrait.webp")
    if portrait is not None:
        return portrait

    cache_path = data_dir / _OFFICIAL_CACHE_NAME if data_dir is not None else None
    if cache_path is not None:
        portrait = await asyncio.to_thread(_cached_portrait, cache_path)
        if portrait is not None:
            return portrait
    try:
        async with session.get(
            OFFICIAL_PORTRAIT_URL,
            headers={"Accept": "image/webp"},
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=5, connect=3),
        ) as response:
            if response.status != 200 or response.content_type != "image/webp":
                raise ValueError("unexpected portrait response")
            if (response.content_length or 0) > _OFFICIAL_MAX_BYTES:
                raise ValueError("portrait response too large")
            data = bytearray()
            async for chunk in response.content.iter_chunked(32 * 1024):
                if len(data) + len(chunk) > _OFFICIAL_MAX_BYTES:
                    raise ValueError("portrait response too large")
                data.extend(chunk)
        raw = bytes(data)
        portrait = await asyncio.to_thread(_official_portrait, raw)
    except (
        aiohttp.ClientError,
        TimeoutError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as error:
        _LOGGER.warning("[美咲日报] 官方立绘暂不可用（%s）", type(error).__name__)
        return None

    if cache_path is not None:
        await asyncio.to_thread(_save_cache, cache_path, raw)
    return portrait
