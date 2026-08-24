"""Small opaque cursors shared by the read-only API endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.api.errors import ApiError

_VERSION = 1
_MAX_TOKEN_BYTES = 4096
_MAX_PAYLOAD_BYTES = 2048
_KEYS = {"v", "e", "p", "c", "f"}


@dataclass(frozen=True, slots=True)
class CursorPayload:
    position: dict[str, Any]
    context: dict[str, Any]


def encode_cursor(
    endpoint: str,
    *,
    position: Mapping[str, Any],
    context: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> str:
    payload = {
        "v": _VERSION,
        "e": endpoint,
        "p": dict(position),
        "c": dict(context),
        "f": _fingerprint(parameters),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Cursor payload is too large")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(
    token: str,
    endpoint: str,
    *,
    parameters: Mapping[str, Any],
) -> CursorPayload:
    try:
        encoded = token.encode("ascii")
        if not encoded or len(encoded) > _MAX_TOKEN_BYTES:
            raise ValueError
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise ValueError
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != _KEYS
            or payload["v"] != _VERSION
            or payload["e"] != endpoint
            or not isinstance(payload["p"], dict)
            or not isinstance(payload["c"], dict)
            or not isinstance(payload["f"], str)
            or payload["f"] != _fingerprint(parameters)
        ):
            raise ValueError
    except (UnicodeEncodeError, binascii.Error, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ApiError(422, "invalid_cursor", "Cursor is invalid for this request") from exc
    return CursorPayload(position=payload["p"], context=payload["c"])


def require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ApiError(422, "invalid_cursor", f"Cursor {name} is invalid")
    return value


def _fingerprint(parameters: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        parameters, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
