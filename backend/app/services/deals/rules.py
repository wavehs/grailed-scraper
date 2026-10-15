"""Is an active listing worth opening? The rules of v2 plan 4.1, free of the database.

A listing is a deal when its model is trusted and liquid (Liquidity Score, D10), sells often
in the listing's condition, the ask is at or under that bucket's 25th percentile of sales,
and reselling at the bucket median nets ``min_margin`` or more after Grailed fees. The seller
must be rated (or trusted) and not a dormant wholesaler, and the listing has no quality flags.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.grouping.kinds import group_kind
from app.services.metrics.fees import FeeSchedule, Resale, resale

MIN_SELLER_RATINGS = 5
MIN_SELLER_RATING = Decimal("4.5")
# An unconfirmed mined model must be backed by this many listings.
MIN_SUPPORT = 10
# Under this share of the bucket median an ask is likelier a fake or a typo than a deal.
FLOOR_RATIO = Decimal("0.3")
TRUSTED_SOURCES = frozenset({"seed", "user"})
# Buckets that are not one model, as in the grouping evaluation; a collab line still counts.
NOT_A_MODEL = frozenset({"none", "descriptor", "review"})

REASONS = (
    "fallback",
    "ignored_group",
    "retired",
    "unproven_group",
    "illiquid",
    "wholesaler",
    "unrated_seller",
    "quality_flags",
    "few_sales",
    "above_p25",
    "too_cheap",
    "thin_margin",
)


@dataclass(frozen=True, slots=True)
class DealRules:
    """The thresholds a person may tune; see ``Settings.deals_*``."""

    min_margin: Decimal = Decimal("0.20")
    min_bucket_sales: int = 20
    min_liquidity_score: Decimal = Decimal("50")


@dataclass(frozen=True, slots=True)
class GroupFacts:
    slug: str
    product_type: str
    status: str
    source: str
    support: int | None
    retired: bool
    is_fallback: bool
    liquidity_score: Decimal | None
    pricing: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Offer:
    """An active listing; ``shipping`` is what the buyer pays on top (USD)."""

    price: Decimal
    shipping: Decimal | None
    condition: str | None
    quality_flags: tuple[str, ...]
    seller_rating_avg: Decimal | None
    seller_rating_count: int | None
    seller_trusted: bool | None
    wholesale: bool


@dataclass(frozen=True, slots=True)
class Judgement:
    """Why the listing is not a deal (empty when it is) and the numbers behind it."""

    reasons: tuple[str, ...]
    bucket: str
    p25: Decimal | None
    p50: Decimal | None
    resale: Resale | None

    @property
    def is_deal(self) -> bool:
        return not self.reasons


def bucket_of(condition: str | None) -> str:
    """The price bucket of a Grailed condition, as in ``metrics/pricing.py``."""

    return "new" if condition == "is_new" else "used"


def judge(offer: Offer, group: GroupFacts, rules: DealRules, schedule: FeeSchedule) -> Judgement:
    reasons = [*group_reasons(group, rules), *seller_reasons(offer)]
    if offer.quality_flags:
        reasons.append("quality_flags")
    bucket = bucket_of(offer.condition)
    stats = group.pricing.get(bucket) or {}
    p25, p50 = _decimal(stats.get("p25")), _decimal(stats.get("p50"))
    sales = int(stats.get("n") or 0)
    if sales < rules.min_bucket_sales or not p25 or not p50 or offer.price <= 0:
        return Judgement((*reasons, "few_sales"), bucket, p25, p50, None)
    if offer.price > p25:
        reasons.append("above_p25")
    if offer.price < p50 * FLOOR_RATIO:
        reasons.append("too_cheap")
    # The resale charges the buyer shipping and pays the label: both left out, as a wash.
    outcome = resale(
        schedule,
        buy=offer.price,
        costs_in=offer.shipping or Decimal(0),
        expected=p50,
        ship_buyer=Decimal(0),
        label_cost=Decimal(0),
    )
    if outcome.roi < rules.min_margin:
        reasons.append("thin_margin")
    return Judgement(tuple(reasons), bucket, p25, p50, outcome)


def group_reasons(group: GroupFacts, rules: DealRules) -> list[str]:
    if group.is_fallback or group_kind(group.slug, group.product_type) in NOT_A_MODEL:
        return ["fallback"]
    reasons = []
    if group.status == "ignored":
        reasons.append("ignored_group")
    if group.retired:
        reasons.append("retired")
    proven = (
        group.status == "confirmed"
        or group.source in TRUSTED_SOURCES
        or (group.support or 0) >= MIN_SUPPORT
    )
    if not proven:
        reasons.append("unproven_group")
    if group.liquidity_score is None or group.liquidity_score < rules.min_liquidity_score:
        reasons.append("illiquid")
    return reasons


def seller_reasons(offer: Offer) -> list[str]:
    reasons = ["wholesaler"] if offer.wholesale else []
    rated = (
        (offer.seller_rating_count or 0) >= MIN_SELLER_RATINGS
        and offer.seller_rating_avg is not None
        and offer.seller_rating_avg >= MIN_SELLER_RATING
    )
    if not (rated or offer.seller_trusted):
        reasons.append("unrated_seller")
    return reasons


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
