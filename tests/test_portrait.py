import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import portrait


class WebPResponse:
    status = 200
    content_type = "image/webp"
    content_length = None

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.content = self

    async def iter_chunked(self, size: int):
        for position in range(0, len(self.data), size):
            yield self.data[position : position + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class PortraitTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_portrait_is_cropped_and_the_verified_raw_image_cached(self):
        with BytesIO() as buffer:
            image = Image.new("RGBA", (59, 600), "purple")
            image.paste("yellow", (0, 590, 59, 600))
            image.save(buffer, "WEBP", lossless=True)
            raw = buffer.getvalue()
        checksum = hashlib.sha256(raw).hexdigest()
        session = MagicMock()
        session.get.return_value = WebPResponse(raw)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(portrait, "ASSETS", Path(temp)),
            patch.object(portrait, "_OFFICIAL_SHA256", checksum),
        ):
            directory = Path(temp)
            loaded = await portrait.load_portrait(session, directory)
            self.assertEqual(loaded.size, (59, 590))
            self.assertEqual(
                loaded.getpixel((0, loaded.height - 1)), (128, 0, 128, 255)
            )
            self.assertEqual(
                (directory / portrait._OFFICIAL_CACHE_NAME).read_bytes(), raw
            )
            self.assertEqual(
                (await portrait.load_portrait(session, directory)).tobytes(),
                loaded.tobytes(),
            )
            session.get.assert_called_once()
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

            Image.new("RGBA", (16, 16), "green").save(
                directory / "portrait.webp", lossless=True
            )
            self.assertEqual(
                (await portrait.load_portrait(session, directory)).getpixel((0, 0)),
                (0, 128, 0, 255),
            )
            session.get.assert_called_once()

    async def test_rejects_unverified_bytes_without_writing_to_disk(self):
        session = MagicMock()
        session.get.return_value = WebPResponse(b"invalid image")
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(portrait, "ASSETS", Path(temp)),
        ):
            directory = Path(temp)
            with self.assertLogs("portrait", "WARNING"):
                self.assertIsNone(await portrait.load_portrait(session, directory))
            self.assertEqual(list(directory.iterdir()), [])
