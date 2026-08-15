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

    listings: Mapped[list[Listing]] = relationship(back_populates="parser_run")
    tasks: Mapped[list[ParserRunTask]] = relationship(back_populates="parser_run")
    scoring_snapshots: Mapped[list[ScoringSnapshot]] = relationship(
        back_populates="parser_run", cascade="all, delete-orphan"
    )


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'sold', 'removed_pending', 'removed')",
            name="ck_listings_status",
        ),
        CheckConstraint("fetch_tier IN ('T1', 'T2', 'T3')", name="ck_listings_fetch_tier"),
        CheckConstraint(
            "seller_identity_mode IN ('none', 'hashed', 'plain')",
            name="ck_listings_seller_identity_mode",
        ),
        CheckConstraint("price > 0", name="ck_listings_price_positive"),
        Index("ix_listings_brand_status_sold_at", "brand_id", "status", "sold_at"),
        Index("ix_listings_status_last_seen_at", "status", "last_seen_at"),
        Index("ix_listings_source_product", "source", "source_product_id"),
        Index("ix_listings_seller_created", "seller_identity", "created_at"),
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
    cover_asset_key: Mapped[str | None] = mapped_column(String(64), index=True)
    cover_content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    cover_dhash: Mapped[str | None] = mapped_column(String(16), index=True)
    photo_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seller_identity: Mapped[str | None] = mapped_column(Text)
    seller_identity_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    seller_country: Mapped[str | None] = mapped_column(String(2))
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fetch_tier: Mapped[str] = mapped_column(String(2), nullable=False)
    parser_run_id: Mapped[int] = mapped_column(
        ForeignKey("parser_runs.id", ondelete="RESTRICT"), nullable=False
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    raw_json_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_version: Mapped[str | None] = mapped_column(String(32))

    brand: Mapped[Brand | None] = relationship(back_populates="listings")
    parser_run: Mapped[ParserRun] = relationship(back_populates="listings")
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
    """Stable analytics segment backed by a rule or a brand/category fallback."""

    __tablename__ = "model_groups"
    __table_args__ = (
        CheckConstraint(
            "group_type IN ('rule', 'fallback', 'source_product', 'resolved')",
            name="ck_model_groups_type",
        ),
        Index("ix_model_groups_brand_type", "brand_id", "group_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(255))
    group_type: Mapped[str] = mapped_column(String(16), nullable=False)
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "model_groups.id", name="fk_model_groups_merged_into", ondelete="SET NULL"
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="model_groups")
    rule: Mapped[ModelRule | None] = relationship(
        back_populates="group", cascade="all, delete-orphan", uselist=False
    )
    snapshots: Mapped[list[ScoringSnapshot]] = relationship(back_populates="model_group")


class ListingModelAssignment(Base):
    """Auditable exact-model assignment; fallback analytics groups are not stored here."""

    __tablename__ = "listing_model_assignments"
    __table_args__ = (Index("ix_listing_model_assignments_group", "model_group_id"),)

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    model_group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="RESTRICT"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PhysicalItem(Base):
    """One physical item across same-seller relists before its sale."""

    __tablename__ = "physical_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PhysicalItemMember(Base):
    __tablename__ = "physical_item_members"
    __table_args__ = (Index("ix_physical_item_members_item", "physical_item_id"),)

    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    physical_item_id: Mapped[int] = mapped_column(
        ForeignKey("physical_items.id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityMatch(Base):
    """Explainable model/relist pair and its automatic or manual decision."""

    __tablename__ = "identity_matches"
    __table_args__ = (
        CheckConstraint("level IN ('model', 'physical')", name="ck_identity_matches_level"),
        CheckConstraint(
            "status IN ('pending', 'auto_confirmed', 'confirmed', 'rejected')",
            name="ck_identity_matches_status",
        ),
        CheckConstraint(
            "relation_type IS NULL OR relation_type = 'relist'",
            name="ck_identity_matches_relation",
        ),
        CheckConstraint("left_listing_id < right_listing_id", name="ck_identity_matches_order"),
        UniqueConstraint(
            "level", "left_listing_id", "right_listing_id", name="uq_identity_matches_pair"
        ),
        Index("ix_identity_matches_queue", "status", "level", "confidence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    left_listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    right_listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRule(Base):
    """User-maintained deterministic title matcher for one model group."""

    __tablename__ = "model_rules"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_model_rules_group"),
        Index("ix_model_rules_brand_active", "brand_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False
    )
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    include_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    exclude_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    category: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    group: Mapped[ModelGroup] = relationship(back_populates="rule")


class ScoringSnapshot(Base):
    """Immutable, versioned score produced for one group, run, and data window."""

    __tablename__ = "scoring_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "parser_run_id",
            "model_group_id",
            "model_version",
            "window_days",
            name="uq_scoring_snapshots_identity",
        ),
        CheckConstraint("window_days IN (30, 90)", name="ck_scoring_snapshots_window"),
        Index(
            "ix_scoring_snapshots_group_window_run",
            "model_group_id",
            "window_days",
            "parser_run_id",
        ),
        Index(
            "ix_scoring_snapshots_brand_window_opportunity",
            "brand_id",
            "window_days",
            "market_opportunity_score",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parser_run_id: Mapped[int] = mapped_column(
        ForeignKey("parser_runs.id", ondelete="CASCADE"), nullable=False
    )
    model_group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="RESTRICT"), nullable=False
    )
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False)
    median_sold_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    median_sold_likes_per_day: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sell_through: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    liquidity_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    price_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    market_opportunity_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    component_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence_factors: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    parser_run: Mapped[ParserRun] = relationship(back_populates="scoring_snapshots")
    model_group: Mapped[ModelGroup] = relationship(back_populates="snapshots")


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
            "fetch_tier IS NULL OR fetch_tier IN ('T1', 'T2', 'T3')",
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
