"""Create phase-two domain tables.

Revision ID: 20260808_0002
Revises: 20260808_0001
Create Date: 2026-08-08 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260808_0002"
down_revision: str | None = "20260808_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create normalized data, discovery cache, and resumable-run tables."""

    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=255), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "parser_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("degraded_mode", sa.Boolean(), nullable=False),
        sa.Column("tier_used", sa.String(length=2)),
        sa.Column("budget_estimate", sa.JSON()),
        sa.Column("requests_made", sa.Integer(), nullable=False),
        sa.Column("coverage_avg", sa.Numeric(precision=6, scale=5)),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("mode IN ('delta', 'full', 'refresh_active')", name="ck_parser_runs_mode"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'interrupted', 'cancelled')",
            name="ck_parser_runs_status",
        ),
    )
    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("grailed_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("brand_name_raw", sa.String(length=255), nullable=False),
        sa.Column("brand_slug", sa.String(length=255)),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(length=255)),
        sa.Column("subcategory", sa.String(length=255)),
        sa.Column("size_raw", sa.String(length=128)),
        sa.Column("size_normalized", sa.String(length=128)),
        sa.Column("condition_raw", sa.String(length=128)),
        sa.Column("condition", sa.String(length=128)),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("price_original", sa.Numeric(precision=14, scale=2)),
        sa.Column("currency_original", sa.String(length=3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8)),
        sa.Column("sold_price", sa.Numeric(precision=14, scale=2)),
        sa.Column("likes_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("sold_at", sa.DateTime(timezone=True)),
        sa.Column("sold_at_is_estimated", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_checked_at", sa.DateTime(timezone=True)),
        sa.Column("days_on_market", sa.Integer()),
        sa.Column("cover_photo_url", sa.Text()),
        sa.Column("photo_urls", sa.JSON(), nullable=False),
        sa.Column("photo_count", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer()),
        sa.Column("seller_username_hash", sa.String(length=64)),
        sa.Column("seller_country", sa.String(length=2)),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.Column("fetch_tier", sa.String(length=2), nullable=False),
        sa.Column(
            "parser_run_id", sa.Integer(), sa.ForeignKey("parser_runs.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'sold', 'removed_pending', 'removed')",
            name="ck_listings_status",
        ),
        sa.CheckConstraint("fetch_tier IN ('T0', 'T1', 'T2', 'T3')", name="ck_listings_fetch_tier"),
        sa.CheckConstraint("price > 0", name="ck_listings_price_positive"),
    )
    op.create_index("ix_listings_grailed_id", "listings", ["grailed_id"])
    op.create_index("ix_listings_brand_status_sold_at", "listings", ["brand_id", "status", "sold_at"])
    op.create_index("ix_listings_status_last_seen_at", "listings", ["status", "last_seen_at"])
    op.create_table(
        "listing_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("parser_runs.id", ondelete="SET NULL")),
    )
    op.create_index(
        "ix_listing_price_history_listing_observed", "listing_price_history", ["listing_id", "observed_at"]
    )
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate_to_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rate_date", "currency", name="uq_fx_rates_date_currency"),
    )
    op.create_table(
        "source_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False, unique=True),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("algolia_agent", sa.Text()),
        sa.Column("active_index", sa.String(length=255)),
        sa.Column("sold_index", sa.String(length=255)),
        sa.Column("sorted_indices", sa.JSON(), nullable=False),
        sa.Column("brand_facet", sa.String(length=255)),
        sa.Column("category_facet", sa.String(length=255)),
        sa.Column("key_acl", sa.JSON(), nullable=False),
        sa.Column("pagination_limit", sa.Integer()),
        sa.Column("max_hits_per_page", sa.Integer()),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovery_method", sa.String(length=32), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "source_schema",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_fields", sa.JSON(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("pagination_strategy", sa.String(length=32)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drift_score", sa.Numeric(precision=6, scale=5)),
    )
    op.create_index("ix_source_schema_source", "source_schema", ["source"])
    op.create_table(
        "schema_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_schema_id", sa.Integer(), sa.ForeignKey("source_schema.id", ondelete="SET NULL")),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_schema_alerts_source", "schema_alerts", ["source"])
    op.create_table(
        "parser_run_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("parser_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="SET NULL")),
        sa.Column("index_type", sa.String(length=32), nullable=False),
        sa.Column("bucket_spec", sa.JSON()),
        sa.Column("cursor", sa.Text()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("hits_collected", sa.Integer(), nullable=False),
        sa.Column("expected_hits", sa.Integer()),
        sa.Column("coverage", sa.Numeric(precision=6, scale=5)),
        sa.Column("fetch_tier", sa.String(length=2)),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'skipped', 'truncated')",
            name="ck_parser_run_tasks_status",
        ),
        sa.CheckConstraint(
            "fetch_tier IS NULL OR fetch_tier IN ('T0', 'T1', 'T2', 'T3')", name="ck_tasks_fetch_tier"
        ),
    )
    op.create_index("ix_parser_run_tasks_run_status", "parser_run_tasks", ["run_id", "status"])
    op.create_table(
        "parser_watermarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index_type", sa.String(length=32), nullable=False),
        sa.Column("last_key_value", sa.Text()),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("full_refresh_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source", "brand_id", "index_type", name="uq_parser_watermarks_scope"),
    )


def downgrade() -> None:
    """Remove only the phase-two domain tables."""

    op.drop_table("parser_watermarks")
    op.drop_index("ix_parser_run_tasks_run_status", table_name="parser_run_tasks")
    op.drop_table("parser_run_tasks")
    op.drop_index("ix_schema_alerts_source", table_name="schema_alerts")
    op.drop_table("schema_alerts")
    op.drop_index("ix_source_schema_source", table_name="source_schema")
    op.drop_table("source_schema")
    op.drop_table("source_credentials")
    op.drop_table("fx_rates")
    op.drop_index("ix_listing_price_history_listing_observed", table_name="listing_price_history")
    op.drop_table("listing_price_history")
    op.drop_index("ix_listings_status_last_seen_at", table_name="listings")
    op.drop_index("ix_listings_brand_status_sold_at", table_name="listings")
    op.drop_index("ix_listings_grailed_id", table_name="listings")
    op.drop_table("listings")
    op.drop_table("parser_runs")
    op.drop_table("brands")
