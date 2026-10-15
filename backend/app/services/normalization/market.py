"""Market fields of a hit: price path, shipping, offer flags and the seller's score.

They used to survive only in ``raw_json``, which retention purges, so they get columns.
None of them names or identifies a seller: they are kept under any ``store_seller_identity``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Any

from app.services.normalization.mapping import SourceMappingConfig
from app.services.normalization.values import CENT, money, text, timestamp

MARKET_COLUMNS = (
    "price_initial",
    "price_drop_count",
    "price_updated_at",
    "bumped_at",
    "sold_shipping_price",
    "ship_domestic",
    "makeoffer",
    "buynow",
    "department",
    "seller_rating_avg",
    "seller_rating_count",
    "seller_txn_count",
    "seller_trusted",
)
_RATING = Decimal("0.001")
_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset({"0", "false", "no"})


def market_fields(
    mapping: SourceMappingConfig,
    payload: dict[str, Any],
    *,
    price_original: Decimal,
    fx_rate: Decimal | None,
) -> dict[str, Any]:
    """Columns from one hit; amounts are converted to USD like ``price`` (``fx_rate`` None = USD).

    ``price_drops`` lists earlier prices, oldest first, sometimes ending with the current one.
    """

    def usd(amount: Decimal | None) -> Decimal | None:
        if amount is None or fx_rate is None:
            return amount
        return (amount * fx_rate).quantize(CENT, ROUND_HALF_UP)

    def value(field: str) -> Any:
        return mapping.value(payload, field)

    drops = _amounts(value("price_drops"))
    path = [*drops, price_original]
    return {
        "price_initial": usd(drops[0] if drops else price_original),
        "price_drop_count": sum(later < earlier for earlier, later in pairwise(path)),
        "price_updated_at": timestamp(value("price_updated_at")),
        "bumped_at": timestamp(value("bumped_at")),
        "sold_shipping_price": usd(_nonnegative_money(value("sold_shipping_price"))),
        "ship_domestic": usd(_nonnegative_money(value("ship_domestic"))),
        "makeoffer": _flag(value("makeoffer")),
        "buynow": _flag(value("buynow")),
        "department": text(value("department")),
        "seller_rating_avg": _rating(value("seller_rating_avg")),
        "seller_rating_count": _count(value("seller_rating_count")),
        "seller_txn_count": _count(value("seller_txn_count")),
        "seller_trusted": _flag(value("seller_trusted")),
    }


def _amounts(value: Any) -> list[Decimal]:
    if not isinstance(value, list):
        return []
    return [amount for amount in map(money, value) if amount is not None and amount > 0]


def _nonnegative_money(value: Any) -> Decimal | None:
    amount = money(value)
    return amount if amount is not None and amount >= 0 else None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    normalized = (text(value) or "").casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return None


def _rating(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        rating = Decimal(str(value)).quantize(_RATING, ROUND_HALF_UP)
    except ArithmeticError:
        return None
    return rating if Decimal(0) <= rating <= Decimal(5) else None


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None
