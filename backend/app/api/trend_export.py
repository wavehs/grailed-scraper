"""CSV export of the trends list (v2 plan 3.6): the list's filters, every matching row.

UTF-8 with a BOM so Excel detects the encoding; comma-separated (RFC 4180). Amounts are
dollars, ratios stay decimal fractions. Text cells that a spreadsheet would read as a
formula (``=``, ``+``, ``-``, ``@``) get a leading apostrophe: names come from listings.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mutation import MutationRoute
from app.api.trend_query import TrendQuery, trend_query, trend_select
from app.api.trends import TrendRow, trend_rows
from app.db.session import get_db

router = APIRouter(prefix="/trends", tags=["trends"], route_class=MutationRoute)
EXPORT_MAX_ROWS = 50_000
# Rows per lookup of versions and parent prices (SQLite caps bound parameters).
LOOKUP_BATCH = 1_000
# Rows per streamed chunk.
CHUNK_ROWS = 500
BOM = "﻿"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CENTS = Decimal(100)
_CENT = Decimal("0.01")

Column = tuple[str, Callable[[TrendRow], object]]


def _dollars(cents: int | None) -> Decimal | None:
    return None if cents is None else (Decimal(cents) / _CENTS).quantize(_CENT)


def _price(row: TrendRow, key: str) -> object:
    return None if row.price is None else getattr(row.price, key)


def _price_dollars(row: TrendRow, key: str) -> Decimal | None:
    value = _price(row, key)
    return _dollars(value) if isinstance(value, int) else None


COLUMNS: tuple[Column, ...] = (
    ("brand", lambda row: row.brand),
    ("name", lambda row: row.name),
    ("product_type", lambda row: row.product_type),
    ("section", lambda row: row.section),
    ("group_id", lambda row: row.group_id),
    ("badges", lambda row: " ".join(row.badges)),
    ("sold", lambda row: row.sold),
    ("sold_7d", lambda row: row.sold_7d),
    ("sold_30d", lambda row: row.sold_30d),
    ("sold_90d", lambda row: row.sold_90d),
    ("median_price_usd", lambda row: _dollars(row.median_price)),
    ("price_change", lambda row: row.price_change),
    ("trend_score", lambda row: row.trend_score),
    ("liquidity_score", lambda row: row.liquidity_score),
    ("p30", lambda row: row.p30),
    ("p30_n", lambda row: row.p30_n),
    ("expected_days", lambda row: row.expected_days),
    ("median_days_to_sell", lambda row: row.median_days_to_sell),
    ("sell_through_30d", lambda row: row.sell_through_30d),
    ("active_now", lambda row: row.active_now),
    ("active_effective", lambda row: row.active_effective),
    ("active_fresh", lambda row: row.active_fresh),
    ("new_listings_14d", lambda row: row.new_listings_14d),
    ("gmv_90d_usd", lambda row: _dollars(row.gmv_90d)),
    ("sold_p25_usd", lambda row: _price_dollars(row, "p25")),
    ("sold_p50_usd", lambda row: _price_dollars(row, "p50")),
    ("sold_p75_usd", lambda row: _price_dollars(row, "p75")),
    ("fresh_ask_p50_usd", lambda row: _price_dollars(row, "fresh_ask_p50")),
    ("offer_discount", lambda row: _price(row, "offer_discount")),
    ("prices_approximate", lambda row: _price(row, "approximate")),
    ("is_new", lambda row: row.is_new),
)


def cell(value: object) -> str:
    """One CSV cell: empty for None, 1/0 for booleans, formula-safe text."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str):
        return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value
    return str(value)


def csv_chunks(rows: Sequence[TrendRow]) -> Iterator[str]:
    """The BOM and header, then ``CHUNK_ROWS`` rows per chunk."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([name for name, _ in COLUMNS])
    yield BOM + buffer.getvalue()
    for start in range(0, len(rows), CHUNK_ROWS):
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerows(
            [cell(value(row)) for _, value in COLUMNS] for row in rows[start : start + CHUNK_ROWS]
        )
        yield buffer.getvalue()


@router.get("/export.csv", response_class=StreamingResponse)
async def export_trends(
    session: Annotated[AsyncSession, Depends(get_db)],
    query: Annotated[TrendQuery, Depends(trend_query)],
) -> StreamingResponse:
    """Every row of the list under ``query`` (up to ``EXPORT_MAX_ROWS``), in its order.

    The rows are read before streaming: the session closes when the handler returns.
    """

    selected = trend_select(query)
    found: list[tuple[Any, ...]] = list(
        (await session.execute(selected.ordered(query).limit(EXPORT_MAX_ROWS))).tuples()
    )
    rows: list[TrendRow] = []
    for start in range(0, len(found), LOOKUP_BATCH):
        batch = found[start : start + LOOKUP_BATCH]
        rows.extend(await trend_rows(session, batch, query, by_period=selected.period is not None))
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        csv_chunks(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="trends-{query.level}-{stamp}.csv"'},
    )
