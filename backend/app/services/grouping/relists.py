"""Same-seller relists: a removed listing re-posted as a new one within 90 days."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

RELIST_WINDOW = timedelta(days=90)
# Runs observe removals late: the new listing may appear shortly before the old one vanished.
OBSERVATION_TOLERANCE = timedelta(days=2)
_ENDED = {"removed", "removed_pending"}


@dataclass(frozen=True, slots=True)
class RelistRow:
    id: int
    grailed_id: int
    seller: str | None
    group_id: int
    status: str
    created_at: datetime
    last_seen_at: datetime
    repost_of: int | None = None


def detect_relists(rows: Sequence[RelistRow]) -> dict[int, int]:
    """Return ``listing id -> first listing id`` for every relisted listing."""

    by_grailed_id = {row.grailed_id: row for row in rows}
    predecessor: dict[int, int] = {}
    used: set[int] = set()
    # Grailed's own repost link is authoritative.
    for row in rows:
        original = by_grailed_id.get(row.repost_of) if row.repost_of else None
        if original is not None and original.id != row.id and original.status != "sold":
            predecessor[row.id] = original.id
            used.add(original.id)
    buckets: dict[tuple[str, int], list[RelistRow]] = defaultdict(list)
    for row in rows:
        if row.seller:
            buckets[(row.seller, row.group_id)].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: (item.created_at, item.id))
        for position, current in enumerate(bucket):
            if current.id in predecessor:
                continue
            for previous in reversed(bucket[:position]):
                if previous.status == "sold":
                    break  # a sale ends the item's life; later listings are other items
                if previous.id in used or previous.status not in _ENDED:
                    continue
                ended = previous.last_seen_at
                if ended - OBSERVATION_TOLERANCE <= current.created_at <= ended + RELIST_WINDOW:
                    predecessor[current.id] = previous.id
                    used.add(previous.id)
                    break
    roots: dict[int, int] = {}
    for listing_id in predecessor:
        root = listing_id
        seen = {root}
        while root in predecessor and predecessor[root] not in seen:
            root = predecessor[root]
            seen.add(root)
        roots[listing_id] = root
    return roots
