"""Transport resilience utilities."""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def retry_after_seconds(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value).timestamp()
            return max(0.0, float(target - time.time()))
        except (TypeError, ValueError):
            return None
