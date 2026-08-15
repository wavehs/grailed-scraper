"""Bounded cover-image fingerprints for already-blocked identity candidates."""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import cast
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.transport.protocols import HttpTransport
from app.services.transport.rate_limiter import RateLimiter

ALLOWED_IMAGE_HOSTS = {"media-assets.grailed.com", "cdn.fs.grailed.com"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageFingerprint:
    content_sha256: str
    dhash: str


async def fingerprint_url(
    transport: HttpTransport, limiter: RateLimiter, url: str
) -> ImageFingerprint | None:
    if not _allowed(url):
        return None
    async with limiter.limit(url):
        response = await transport.request("GET", url, timeout_s=15)
    if response.status_code != 200 or not _allowed(response.url):
        return None
    content_type = response.headers.get("content-type", "").partition(";")[0].casefold()
    if not content_type.startswith("image/") or len(response.content) > MAX_IMAGE_BYTES:
        return None
    try:
        return fingerprint_bytes(response.content)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return None


def fingerprint_bytes(content: bytes) -> ImageFingerprint:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("invalid image size")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content)) as source:
            oriented = ImageOps.exif_transpose(source)
            try:
                gray = oriented.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                pixels = cast(list[int], list(gray.get_flattened_data()))
            finally:
                oriented.close()
    bits = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return ImageFingerprint(hashlib.sha256(content).hexdigest(), f"{bits:016x}")


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme.casefold() == "https" and parts.hostname in ALLOWED_IMAGE_HOSTS
