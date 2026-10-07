"""A light copy of one brand's grouping inputs for read-only experiments and baselines.

The copy holds the brand, its listings (without raw payloads), curated groups, overrides,
stop words and designer mappings. ``keep_auto=False`` drops mined groups and every
assignment, so a regroup of the copy shows what the rules alone produce from scratch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.db.models import Base

# Columns replaced in the copy: raw payloads are large and grouping never reads them.
_LISTING_OVERRIDES = {"raw_json": "'{}'", "description": "NULL", "parser_run_id": "NULL"}


def make_scratch(source: Path, target: Path, brand_slug: str, *, keep_auto: bool) -> int:
    """Copy ``brand_slug`` from ``source`` (opened read-only) into a new ``target``."""

    if target.exists():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=rw", uri=True)
    try:
        connection.execute(
            "ATTACH DATABASE ? AS src", (f"file:{source.resolve().as_posix()}?mode=ro",)
        )
        row = connection.execute(
            "SELECT id FROM src.brands WHERE slug = ?", (brand_slug,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Unknown brand {brand_slug}")
        brand_id = int(row[0])
        _copy(connection, "brands", "id = ?", (brand_id,), {"grouping_hash": "NULL"})
        _copy(connection, "brand_source_map", "brand_id = ?", (brand_id,))
        source_tables = {
            row[0] for row in connection.execute("SELECT name FROM src.sqlite_master")
        }
        if "model_blocklist" in source_tables:
            _copy(connection, "model_blocklist", "brand_id = ?", (brand_id,))
        elif "brand_stopwords" in source_tables:  # a database before grouping-v7
            connection.execute(
                "INSERT INTO main.model_blocklist (brand_id, phrase, created_at) "
                "SELECT brand_id, phrase, created_at FROM src.brand_stopwords WHERE brand_id = ?",
                (brand_id,),
            )
        _copy(connection, "listings", "brand_id = ?", (brand_id,), _LISTING_OVERRIDES)
        groups = "brand_id = ?"
        if not keep_auto:
            groups += " AND NOT (status = 'auto' AND source = 'mined')"
        _copy(connection, "model_groups", groups, (brand_id,))
        connection.execute(
            "UPDATE model_groups SET parent_id = NULL "
            "WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM model_groups)"
        )
        _copy(
            connection,
            "listing_overrides",
            "model_group_id IN (SELECT id FROM main.model_groups)",
            (),
        )
        if "parent_overrides" in source_tables:  # absent in a database before grouping-v7
            _copy(
                connection,
                "parent_overrides",
                "group_id IN (SELECT id FROM main.model_groups) AND (parent_id IS NULL "
                "OR parent_id IN (SELECT id FROM main.model_groups))",
                (),
            )
        if keep_auto:
            _copy(
                connection,
                "listing_model_assignments",
                "listing_id IN (SELECT id FROM main.listings)",
                (),
            )
        connection.commit()
        count = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        connection.execute("DETACH DATABASE src")
    finally:
        connection.close()
    return int(count)


def _copy(
    connection: sqlite3.Connection,
    table: str,
    where: str,
    parameters: tuple[object, ...],
    replace: dict[str, str] | None = None,
) -> None:
    target = [row[1] for row in connection.execute(f"PRAGMA main.table_info({table})")]
    source = {row[1] for row in connection.execute(f"PRAGMA src.table_info({table})")}
    columns = [name for name in target if name in source]
    values = [(replace or {}).get(name, name) for name in columns]
    connection.execute(
        f"INSERT INTO main.{table} ({', '.join(columns)}) "
        f"SELECT {', '.join(values)} FROM src.{table} WHERE {where}",
        parameters,
    )
