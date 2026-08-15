"""Build stable field maps and detect schema drift without retaining sensitive examples."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlencode

from app.services.sources.grailed.discovery.client import DiscoveryAlgoliaClient
from app.services.sources.grailed.discovery.models import SchemaChange, SchemaSample

SENSITIVE_PARTS = {
    "username",
    "email",
    "token",
    "key",
    "authorization",
    "cookie",
    "latitude",
    "longitude",
    "coordinates",
    "address",
    "city",
    "state",
    "postal_code",
    "postcode",
    "zip",
    "zipcode",
    "location",
}


async def sample_schema(
    client: DiscoveryAlgoliaClient, index: str, *, sample_size: int
) -> SchemaSample:
    params = urlencode({"query": "", "hitsPerPage": sample_size, "attributesToRetrieve": '["*"]'})
    payload = await client.json("POST", client.index_path(index), json_body={"params": params})
    hits = payload.get("hits", []) if payload else []
    records = [item for item in hits if isinstance(item, dict)]
    counts: Counter[str] = Counter()
    types: defaultdict[str, set[str]] = defaultdict(set)
    examples: dict[str, Any] = {}
    for record in records:
        observed = _flatten(record)
        counts.update(observed.keys())
        for path, value in observed.items():
            types[path].add(_type_name(value))
            if path not in examples and not _sensitive(path):
                examples[path] = _safe_example(value)
    denominator = len(records) or 1
    fields = {
        path: {
            "count": count,
            "frequency": round(count / denominator, 5),
            "types": sorted(types[path]),
            "example": examples.get(path),
        }
        for path, count in sorted(counts.items())
    }
    return SchemaSample(fields=fields, sample_size=len(records))


def compare_schemas(
    previous: dict[str, dict[str, Any]] | None,
    current: dict[str, dict[str, Any]],
) -> tuple[float, tuple[SchemaChange, ...]]:
    if not previous:
        return 0.0, ()
    old_paths, new_paths = set(previous), set(current)
    changes: list[SchemaChange] = [
        SchemaChange("high", "removed", path, before=_types(previous[path]))
        for path in sorted(old_paths - new_paths)
    ]
    changes.extend(
        SchemaChange("info", "added", path, after=_types(current[path]))
        for path in sorted(new_paths - old_paths)
    )
    for path in sorted(old_paths & new_paths):
        before, after = _types(previous[path]), _types(current[path])
        if before != after:
            changes.append(SchemaChange("high", "type_changed", path, before, after))
    union = old_paths | new_paths
    drift = len(changes) / len(union) if union else 0.0
    return drift, tuple(changes)


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            child = f"{path}.{key}" if path else str(key)
            result.update(_flatten(nested, child))
        return result
    if isinstance(value, list):
        result = {}
        for nested in value[:10]:
            result.update(_flatten(nested, f"{path}[]"))
        return result or {path: []}
    return {path: value}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _safe_example(value: Any) -> Any:
    if isinstance(value, str):
        return value[:120]
    return value if isinstance(value, (bool, int, float)) or value is None else None


def _sensitive(path: str) -> bool:
    parts = [part.casefold() for part in path.replace("[]", "").split(".")]
    return any(part in SENSITIVE_PARTS for part in parts) or ("seller" in parts and "id" in parts)


def _types(metadata: dict[str, Any]) -> tuple[str, ...]:
    values = metadata.get("types", [])
    return tuple(str(item) for item in values) if isinstance(values, list) else ()
