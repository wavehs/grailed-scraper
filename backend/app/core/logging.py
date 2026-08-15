"""Structured JSON logging with one central secret-redaction policy."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from logging.handlers import RotatingFileHandler
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from app.core.config import Settings

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "cookie",
    "password",
    "proxy",
    "proxy_url",
    "secret",
    "token",
    "seller_identity_salt",
)
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+")
_KEY_PATH = re.compile(r"(?i)(/1/keys/)[^?\s>]+")
_ALGOLIA_QUERY = re.compile(r"(?i)(x-algolia-api-key=)([^&#\s]+)")
_URL = re.compile(r"https?://[^\s\"']+")
_DEFAULT_FIELDS: dict[str, object] = {
    "request_id": None,
    "run_id": None,
    "task_id": None,
    "source": None,
    "brand": None,
    "index": None,
    "tier": None,
    "duration_ms": None,
}


def _is_sensitive_key(key: object) -> bool:
    key_name = str(key).lower().replace("-", "_")
    return any(part in key_name for part in SENSITIVE_KEY_PARTS)


def mask_secret(value: object) -> str:
    text = str(value)
    return f"{text[:4]}****" if len(text) > 4 else "****"


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"***:***@{hostname}{port}" if parts.username or parts.password else parts.netloc
    query = urlencode(
        [
            (key, mask_secret(item) if _is_sensitive_key(key) else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_text(value: str) -> str:
    value = _BEARER.sub(lambda match: f"{match.group(1)} ****", value)
    value = _KEY_PATH.sub(r"\1****", value)
    value = _ALGOLIA_QUERY.sub(lambda match: f"{match.group(1)}****", value)
    return _URL.sub(lambda match: _redact_url(match.group(0)), value)


def mask_sensitive_data(value: object) -> object:
    """Return a recursively redacted copy suitable for logs and public errors."""

    if isinstance(value, Mapping):
        return {
            key: mask_secret(item) if _is_sensitive_key(key) else mask_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def add_required_fields(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event = str(event_dict.get("event", "log"))
    for key, default in _DEFAULT_FIELDS.items():
        event_dict.setdefault(key, default)
    event_dict["event"] = event
    event_dict.setdefault("msg", event)
    return event_dict


def redact_event(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], mask_sensitive_data(event_dict))


def configure_logging(settings: Settings) -> None:
    """Configure console and rotating local JSON logs."""

    settings.log_directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    parser_file = RotatingFileHandler(
        settings.log_directory / "parser.jsonl",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    parser_file.setFormatter(formatter)
    errors = RotatingFileHandler(
        settings.log_directory / "errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [console, parser_file, errors]
    root.setLevel(settings.log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", key="ts", utc=True),
            structlog.stdlib.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            add_required_fields,
            redact_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
