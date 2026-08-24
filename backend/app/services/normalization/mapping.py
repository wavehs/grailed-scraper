"""Typed declarative source-field mapping loaded from YAML."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import PROJECT_ROOT, RESOURCE_ROOT

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")


class SourceMappingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str
    schema_version: int = Field(default=1, ge=1)
    fields: dict[str, list[str]]
    conditions: dict[str, str] = Field(default_factory=dict)

    def value(self, payload: dict[str, Any], logical_field: str) -> Any:
        for candidate in self.fields.get(logical_field, []):
            if candidate.startswith("_default:"):
                return candidate.partition(":")[2]
            value = resolve_path(payload, candidate)
            if not _empty(value):
                return value
        return None


def load_source_mapping(path: Path | None = None) -> SourceMappingConfig:
    candidates = [
        path,
        RESOURCE_ROOT / "config" / "sources" / "grailed.yaml",
        PROJECT_ROOT / "config" / "sources" / "grailed.yaml",
        Path(__file__).resolve().parents[4] / "config" / "sources" / "grailed.yaml",
    ]
    config_path = next(
        (p for p in candidates if p and Path(p).is_file()),
        PROJECT_ROOT / "config" / "sources" / "grailed.yaml",
    )
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Source mapping {config_path} must contain a YAML object")
    return SourceMappingConfig.model_validate(payload)


def resolve_path(payload: Any, path: str) -> Any:
    tokens: list[str | int] = []
    for match in _TOKEN.finditer(path):
        name, index = match.groups()
        tokens.append(name if name is not None else (index if index == "*" else int(index)))
    return _resolve_tokens(payload, tokens)


def _resolve_tokens(value: Any, tokens: list[str | int]) -> Any:
    if not tokens:
        return value
    head, *tail = tokens
    if head == "*":
        if not isinstance(value, list):
            return None
        flattened: list[Any] = []
        for item in value:
            resolved = _resolve_tokens(item, tail)
            if isinstance(resolved, list):
                flattened.extend(resolved)
            elif not _empty(resolved):
                flattened.append(resolved)
        return flattened
    if isinstance(head, int):
        if not isinstance(value, list) or head >= len(value):
            return None
        return _resolve_tokens(value[head], tail)
    if not isinstance(value, dict) or head not in value:
        return None
    return _resolve_tokens(value[head], tail)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}
