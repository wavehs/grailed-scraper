"""Lenient readers for untrusted source values: money, timestamps and text."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from dateutil.parser import isoparse  # type: ignore[import-untyped]

from app.domain.listings import to_utc_datetime

CENT = Decimal("0.01")


def money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return to_utc_datetime(value)
    if isinstance(value, (int, float, Decimal)):
        numeric = Decimal(str(value))
        if numeric <= 0:
            return None
        if abs(numeric) > Decimal("100000000000"):
            numeric /= Decimal(1000)
        try:
            return datetime.fromtimestamp(float(numeric), tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return to_utc_datetime(isoparse(value))
        except (TypeError, ValueError, OverflowError):
            try:
                return timestamp(Decimal(value))
            except InvalidOperation:
                return None
    return None


def text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None
