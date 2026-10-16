"""SQLAlchemy domain tables for persisted parser data.

The schema intentionally keeps source payloads alongside normalized values.  This
makes a parsing result auditable when Grailed changes a field without silently
discarding the original response.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all persistence models."""


class AppSetting(Base):
    """A validated, non-secret runtime override layered on top of env settings."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str | None] = mapped_column(String(255), unique=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    include_subbrands: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Hash of the grouping rules used by the last regroup; unchanged rules allow a delta pass.
    grouping_hash: Mapped[str | None] = mapped_column(String(64))
    grouped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    listings: Mapped[list[Listing]] = relationship(back_populates="brand")
    source_mappings: Mapped[list[BrandSourceMap]] = relationship(
        back_populates="brand", cascade="all, delete-orphan"
    )
    model_groups: Mapped[list[ModelGroup]] = relationship(back_populates="brand")


class ParserRun(Base):
    __tablename__ = "parser_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('delta', 'full', 'refresh_active')", name="ck_parser_runs_mode"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed', "
            "'interrupted', 'cancelled')",
            name="ck_parser_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="grailed")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="delta")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="planning")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    degraded_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tier_used: Mapped[str | None] = mapped_column(String(2))
    budget_estimate: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    requests_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_avg: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    listings: Mapped[list[Listing]] = relationship(
        back_populates="parser_run", passive_deletes=True
    )
    tasks: Mapped[list[ParserRunTask]] = relationship(
        back_populates="parser_run", passive_deletes=True
    )


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'sold', 'removed_pending', 'removed')",
            name="ck_listings_status",
        ),
        CheckConstraint("fetch_tier = 'T1'", name="ck_listings_fetch_tier"),
        CheckConstraint(
            "seller_identity_mode IN ('none', 'hashed', 'plain')",
            name="ck_listings_seller_identity_mode",
        ),
        CheckConstraint("price > 0", name="ck_listings_price_positive"),
        Index("ix_listings_brand_status_sold_at", "brand_id", "status", "sold_at"),
        Index("ix_listings_status_last_seen_at", "status", "last_seen_at"),
        Index("ix_listings_status_id", "status", "id"),
        Index("ix_listings_source_product", "source", "source_product_id"),
        Index("ix_listings_seller_created", "seller_identity", "created_at"),
        Index("ix_listings_brand_type_status", "brand_id", "product_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="grailed")
    grailed_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    brand_name_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_slug: Mapped[str | None] = mapped_column(String(255))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    category: Mapped[str | None] = mapped_column(String(255))
    subcategory: Mapped[str | None] = mapped_column(String(255))
    category_path: Mapped[str | None] = mapped_column(String(255))
    # Taxonomy type set by grouping (config/taxonomy.yaml), never by the parser.
    product_type: Mapped[str | None] = mapped_column(String(32))
    size_raw: Mapped[str | None] = mapped_column(String(128))
    size_normalized: Mapped[str | None] = mapped_column(String(128))
    condition_raw: Mapped[str | None] = mapped_column(String(128))
    condition: Mapped[str | None] = mapped_column(String(128))
    color: Mapped[str | None] = mapped_column(String(128))
    source_product_id: Mapped[int | None] = mapped_column(Integer)
    source_sku_id: Mapped[int | None] = mapped_column(Integer)
    source_repost_id: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    price_original: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency_original: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    sold_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    likes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_on_market: Mapped[int | None] = mapped_column(Integer)
    cover_photo_url: Mapped[str | None] = mapped_column(Text)
    photo_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    designer_names: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seller_identity: Mapped[str | None] = mapped_column(Text)
    seller_identity_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    seller_country: Mapped[str | None] = mapped_column(String(2))
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fetch_tier: Mapped[str] = mapped_column(String(2), nullable=False)
    parser_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("parser_runs.id", ondelete="SET NULL")
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    raw_json_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Same-seller relist: points at the first listing of the item (time to sell starts there).
    relist_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", name="fk_listings_relist_of", ondelete="SET NULL")
    )
    # Market fields kept out of raw_json, which retention purges (migration 0022).
    price_initial: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_drop_count: Mapped[int | None] = mapped_column(Integer)
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bumped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_shipping_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    ship_domestic: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    makeoffer: Mapped[bool | None] = mapped_column(Boolean)
    buynow: Mapped[bool | None] = mapped_column(Boolean)
    department: Mapped[str | None] = mapped_column(String(32))
    seller_rating_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    seller_rating_count: Mapped[int | None] = mapped_column(Integer)
    seller_txn_count: Mapped[int | None] = mapped_column(Integer)
    seller_trusted: Mapped[bool | None] = mapped_column(Boolean)

    brand: Mapped[Brand | None] = relationship(back_populates="listings")
    parser_run: Mapped[ParserRun | None] = relationship(back_populates="listings")
    price_history: Mapped[list[ListingPriceHistory]] = relationship(back_populates="listing")


class ListingPriceHistory(Base):
    __tablename__ = "listing_price_history"
    __table_args__ = (
        Index("ix_listing_price_history_listing_observed", "listing_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("parser_runs.id", ondelete="SET NULL")
    )

    listing: Mapped[Listing] = relationship(back_populates="price_history")


class ModelGroup(Base):
    """One model of a brand and product type: a line (no parent) or a version inside it."""

    __tablename__ = "model_groups"
    __table_args__ = (
        UniqueConstraint("brand_id", "product_type", "slug", name="uq_model_groups_identity"),
        CheckConstraint(
            "status IN ('confirmed', 'auto', 'ignored')", name="ck_model_groups_status"
        ),
        CheckConstraint(
            "source IN ('seed', 'mined', 'user', 'system')", name="ck_model_groups_source"
        ),
        Index("ix_model_groups_brand_type", "brand_id", "product_type"),
        Index("ix_model_groups_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    product_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "_none" is the per-type "No model" bucket; other slugs are normalized model phrases.
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", name="fk_model_groups_parent", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="mined")
    # Seeded hint: an ambiguous category without type words takes this model's type.
    infer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    support: Mapped[int | None] = mapped_column(Integer)
    # An auto group a full pass no longer derives: hidden, kept for the TTL with its id.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="model_groups")


class ListingModelAssignment(Base):
    """Current group of every grouped listing; recomputed by grouping, never edited directly."""

    __tablename__ = "listing_model_assignments"
    __table_args__ = (Index("ix_listing_model_assignments_group", "model_group_id"),)

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    model_group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ListingOverride(Base):
    """A manual "move this listing to that group" rule that survives regrouping."""

    __tablename__ = "listing_overrides"

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    model_group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParentOverride(Base):
    """A manual "this group belongs to that line" rule; ``parent_id`` NULL makes it a line."""

    __tablename__ = "parent_overrides"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), primary_key=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelBlock(Base):
    """A phrase the user marked as "not a model": never a model, still part of titles."""

    __tablename__ = "model_blocklist"
    __table_args__ = (
        UniqueConstraint("brand_id", "phrase", name="uq_model_blocklist_phrase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    phrase: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GroupMetric(Base):
    """Current market metrics of one model line/version, product type or brand.

    Recomputed from all listings after every collection and regroup (docs/METRICS.md);
    no per-run snapshots, so every brand is always visible.
    """

    __tablename__ = "group_metrics"
    __table_args__ = (
        CheckConstraint("scope IN ('model', 'type', 'brand')", name="ck_group_metrics_scope"),
        Index("ix_group_metrics_scope_liquidity", "scope", "liquidity_score"),
        Index("ix_group_metrics_brand_scope", "brand_id", "scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "model:<group id>", "type:<brand id>:<type>" or "brand:<brand id>".
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    product_type: Mapped[str | None] = mapped_column(String(32))
    section: Mapped[str | None] = mapped_column(String(32))
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE")
    )
    is_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    listings: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_7d: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_prev_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_90d: Mapped[int] = mapped_column(Integer, nullable=False)
    weekly_sales: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    weekly_median_price: Mapped[list[str | None]] = mapped_column(JSON, nullable=False)
    median_price_7d: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_price_30d: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_price_90d: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    median_days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    active_now: Mapped[int] = mapped_column(Integer, nullable=False)
    new_listings_14d: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_through_30d: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    growth: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    speed: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    # Sales of every rankable period: {"30": {"sold", "price", "days", "likes"}}.
    windows: Mapped[dict[str, dict[str, Any]] | None] = mapped_column(JSON)
    # The ranking period (liquidity policy) copied out of ``windows`` for sorting.
    period_sold: Mapped[int | None] = mapped_column(Integer)
    period_median_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    period_median_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    period_median_likes: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    colors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    sizes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # v2 plan 2.1-2.4; empty on rows computed before migration 0024.
    active_effective: Mapped[int | None] = mapped_column(Integer)
    active_fresh: Mapped[int | None] = mapped_column(Integer)
    p30_sold: Mapped[int | None] = mapped_column(Integer)
    p30_cohort: Mapped[int | None] = mapped_column(Integer)
    # Smoothed towards the parent by the cross-brand pass; None until it runs.
    p30: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    gmv_90d: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # Per condition bucket ("all", "new", "used"): see services/metrics/pricing.py.
    pricing: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    department: Mapped[str | None] = mapped_column(String(32))
    # 0-100 among the rows of the same scope that pass the threshold of the liquidity policy
    # (services/metrics/liquidity.py); added by 0024, ranked this way since 0029.
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    # Model badges (services/metrics/badges.py); None on other scopes and before 0026.
    badges: Mapped[list[str] | None] = mapped_column(JSON)


class GroupMetricDaily(Base):
    """One day's copy of a scope's ``group_metrics`` row, kept two years (v2 plan 1.3).

    Models are kept with a sale in 90 days or at least 20 active listings; types and brands
    always. ``active_effective``, ``active_fresh``, ``p30`` and ``liquidity_score`` are filled
    once those metrics exist and stay empty on earlier days.
    """

    __tablename__ = "group_metrics_daily"
    __table_args__ = (
        UniqueConstraint("scope_key", "snapshot_date", name="uq_group_metrics_daily_scope_day"),
        Index("ix_group_metrics_daily_brand_day", "brand_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    # History outlives a deleted group; ``scope_key`` still names it.
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", ondelete="SET NULL")
    )
    sold_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_90d: Mapped[int] = mapped_column(Integer, nullable=False)
    active_now: Mapped[int] = mapped_column(Integer, nullable=False)
    active_effective: Mapped[int | None] = mapped_column(Integer)
    active_fresh: Mapped[int | None] = mapped_column(Integer)
    median_price_30d: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    sell_through_30d: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    trend_score: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    p30: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))


class SeasonProfile(Base):
    """Sales by calendar month of the market, a type, a brand's type or a model (plan 2.5).

    Rebuilt from every sale after each recompute. ``months`` and ``seasons`` hold the
    indices; a NULL ``department`` or ``product_type`` spans all of them.
    """

    __tablename__ = "season_profiles"
    __table_args__ = (
        UniqueConstraint("profile_key", name="uq_season_profiles_key"),
        CheckConstraint(
            "kind IN ('market', 'type', 'brand', 'model')", name="ck_season_profiles_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_key: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE")
    )
    product_type: Mapped[str | None] = mapped_column(String(32))
    department: Mapped[str | None] = mapped_column(String(32))
    months: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    seasons: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    months_covered: Mapped[int] = mapped_column(Integer, nullable=False)
    sales_year: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    correlation: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    stable: Mapped[bool | None] = mapped_column(Boolean)
    blend_weight: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GroupMonthStat(Base):
    """Sales of one scope in one calendar month (UTC) by condition; rebuilt with the metrics."""

    __tablename__ = "group_month_stats"
    __table_args__ = (
        UniqueConstraint(
            "scope_key", "month", "cond_bucket", name="uq_group_month_stats_scope_month"
        ),
        CheckConstraint(
            "cond_bucket IN ('all', 'new', 'used')", name="ck_group_month_stats_bucket"
        ),
        Index("ix_group_month_stats_brand", "brand_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE")
    )
    product_type: Mapped[str | None] = mapped_column(String(32))
    # First day of the month.
    month: Mapped[date] = mapped_column(Date, nullable=False)
    cond_bucket: Mapped[str] = mapped_column(String(4), nullable=False)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Prices leave out lots and outliers; the count keeps them.
    gmv: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    p25: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    p50: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    p75: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("rate_date", "currency", name="uq_fx_rates_date_currency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_to_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrandSourceMap(Base):
    __tablename__ = "brand_source_map"
    __table_args__ = (
        UniqueConstraint(
            "brand_id",
            "source",
            "source_designer_name",
            name="uq_brand_source_map_identity",
        ),
        CheckConstraint(
            "NOT (verified = 1 AND rejected_at IS NOT NULL)",
            name="ck_brand_source_map_state",
        ),
        Index("ix_brand_source_map_brand_verified", "brand_id", "verified"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="grailed")
    source_designer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_slug: Mapped[str | None] = mapped_column(String(255))
    source_designer_id: Mapped[str | None] = mapped_column(String(128))
    listings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_subbrand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="source_mappings")


class UnmatchedBrand(Base):
    __tablename__ = "unmatched_brands"
    __table_args__ = (
        UniqueConstraint("source", "raw_name", name="uq_unmatched_brands_source_name"),
        Index("ix_unmatched_brands_source_normalized", "source", "normalized_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="grailed")
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    suggested_brand_id: Mapped[int | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL")
    )
    best_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceCredential(Base):
    __tablename__ = "source_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    algolia_agent: Mapped[str | None] = mapped_column(Text)
    active_index: Mapped[str | None] = mapped_column(String(255))
    sold_index: Mapped[str | None] = mapped_column(String(255))
    sorted_indices: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    brand_facet: Mapped[str | None] = mapped_column(String(255))
    category_facet: Mapped[str | None] = mapped_column(String(255))
    key_acl: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pagination_limit: Mapped[int | None] = mapped_column(Integer)
    max_hits_per_page: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discovery_method: Mapped[str] = mapped_column(String(32), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)


class SourceSchema(Base):
    __tablename__ = "source_schema"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    pagination_strategy: Mapped[str | None] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    drift_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))


class SchemaAlert(Base):
    __tablename__ = "schema_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_schema.id", ondelete="SET NULL")
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParserRunTask(Base):
    __tablename__ = "parser_run_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'skipped', 'truncated')",
            name="ck_parser_run_tasks_status",
        ),
        CheckConstraint(
            "fetch_tier IS NULL OR fetch_tier = 'T1'",
            name="ck_tasks_fetch_tier",
        ),
        Index("ix_parser_run_tasks_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("parser_runs.id", ondelete="CASCADE"), nullable=False
    )
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    index_type: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hits_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_hits: Mapped[int | None] = mapped_column(Integer)
    # "estimated" when the source could only estimate expected_hits; null before 0021.
    expected_source: Mapped[str | None] = mapped_column(String(16))
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    fetch_tier: Mapped[str | None] = mapped_column(String(2))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parser_run: Mapped[ParserRun] = relationship(back_populates="tasks")


class ParserWatermark(Base):
    __tablename__ = "parser_watermarks"
    __table_args__ = (
        UniqueConstraint("source", "brand_id", "index_type", name="uq_parser_watermarks_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    index_type: Mapped[str] = mapped_column(String(32), nullable=False)
    last_key_value: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistItem(Base):
    """A model the user follows (v2 plan 3.7); changes are measured from ``added_at``."""

    __tablename__ = "watchlist"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))


class DealCandidate(Base):
    """A listing that passed the deal rules (v2 plan 4.1), one row per listing.

    Amounts are USD; ``verdict`` is a person's rating ("good" = worth opening) for the
    acceptance check of the first 50 candidates.
    """

    __tablename__ = "deal_candidates"
    __table_args__ = (
        CheckConstraint("bucket IN ('new', 'used')", name="ck_deal_candidates_bucket"),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('good', 'bad')", name="ck_deal_candidates_verdict"
        ),
        Index("ix_deal_candidates_found", "found_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p25: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    roi: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(8))


class DealScan(Base):
    """Where the scanner stopped for a brand: hits created or repriced after ``scanned_to``."""

    __tablename__ = "deal_scans"

    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True
    )
    scanned_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500))

