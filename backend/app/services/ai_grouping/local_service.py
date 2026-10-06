"""Local inference with persisted per-input progress and shared atomic application."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AiGroupingBatch,
    AiGroupingItem,
    AiGroupingRun,
    Brand,
    Listing,
    ListingModelAssignment,
    ModelGroup,
)
from app.services.ai_grouping.domain import compute_input_hash
from app.services.ai_grouping.local_client import LocalDecision, LocalGroupingClient
from app.services.ai_grouping.safety import (
    LOCAL_GROUPING_VERSION,
    LOCAL_KEY_PREFIX,
    canonical_model,
    local_input_version,
    model_evidence_valid,
    product_type,
)
from app.services.ai_grouping.service import (
    _ACTIVE_STATES,
    _BLOCKING_BATCH_STATES,
    AiGroupingService,
    GroupingMode,
)


class LocalGroupingService(AiGroupingService):
    grouping_version = LOCAL_GROUPING_VERSION
    prompt_version = "local-prompt-v1"
    key_prefix = LOCAL_KEY_PREFIX
    method_prefix = "ollama"

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        client: LocalGroupingClient,
    ) -> None:
        super().__init__(sessions, settings)
        self._local_client = client
        self.base_model = self.review_model = settings.ollama_model
        self._model_run_ids: set[int] = set()

    async def _selected_rows(
        self,
        session: AsyncSession,
        mode: GroupingMode,
    ) -> list[tuple[Listing, Brand, ListingModelAssignment | None, ModelGroup | None]]:
        try:
            digest = await self._local_client.readiness()
        except RuntimeError:
            digest = ""
        self._model_run_ids = set(
            await session.scalars(
                select(AiGroupingRun.id).where(
                    AiGroupingRun.status == "completed",
                    AiGroupingRun.grouping_version == self.grouping_version,
                    AiGroupingRun.base_model == self.base_model,
                    AiGroupingRun.stats["model_digest"].as_string() == digest,
                )
            )
        )
        return await super()._selected_rows(session, mode)

    def _assignment_model_current(self, assignment: ListingModelAssignment) -> bool:
        return assignment.ai_grouping_run_id in self._model_run_ids

    async def _canary_completed(self, session: AsyncSession) -> bool:
        return (
            await session.scalar(
                select(AiGroupingRun.id)
                .where(
                    AiGroupingRun.id.in_(self._model_run_ids),
                    AiGroupingRun.mode == "canary",
                )
                .limit(1)
            )
            is not None
        )

    async def preflight(self, mode: GroupingMode) -> dict[str, Any]:
        blocked = None
        try:
            await self._local_client.readiness()
        except RuntimeError as exc:
            blocked = str(exc)
        configured = blocked is None
        async with self._sessions() as session:
            rows = await self._selected_rows(session, mode)
            unique = {self._input_hash(listing, brand.name) for listing, brand, _, _ in rows}
            active = await session.scalar(
                select(AiGroupingRun.id).where(AiGroupingRun.status.in_(_ACTIVE_STATES)).limit(1)
            )
            batch = await session.scalar(
                select(AiGroupingBatch.id)
                .where(AiGroupingBatch.status.in_(_BLOCKING_BATCH_STATES))
                .limit(1)
            )
            if active is not None or batch is not None:
                blocked = blocked or "grouping_run_active"
            if not rows:
                blocked = blocked or "nothing_to_group"
            if mode != "canary" and not await self._canary_completed(session):
                blocked = blocked or "canary_required"
        return {
            "mode": mode,
            "provider": "ollama",
            "provider_configured": configured,
            "gemini_configured": False,
            "listing_count": len(rows),
            "unique_input_count": len(unique),
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_usd": Decimal(0),
            "budget_cap_usd": Decimal(0),
            "can_start": blocked is None,
            "blocked_reason": blocked,
            "data_fields": ["brand", "category", "subcategory", "title", "locked_product_type"],
        }

    async def _cached_input_hashes(self, session: AsyncSession) -> set[str]:
        # Completed assignments already leave remaining/pending. Do not reuse results
        # across runs until model digests, not mutable tags, are part of cache selection.
        return set()

    async def _reuse_cached_results(self, session: AsyncSession, run_id: int, now: datetime) -> int:
        return 0

    def _input_hash(self, listing: Listing, brand: str) -> str:
        return compute_input_hash(
            brand=brand,
            category=listing.category,
            subcategory=listing.subcategory,
            title=listing.title,
            prompt_version=f"{local_input_version()}:{self.base_model}",
        )

    @staticmethod
    def _product_type(listing: Listing, brand: str) -> str | None:
        return product_type(listing.title, brand, listing.category, listing.subcategory)

    async def process(
        self,
        run_id: int,
        client: Any,
        *,
        market_lock: asyncio.Lock | None = None,
        cancelled: asyncio.Event | None = None,
        **kwargs: Any,
    ) -> None:
        stop = cancelled or asyncio.Event()
        digest = await self._local_client.readiness()
        async with self._sessions() as session:
            run = await session.get(AiGroupingRun, run_id)
            if run is None or run.grouping_version != self.grouping_version:
                raise RuntimeError("grouping_provider_mismatch")
            if run.base_model != self.base_model or (
                run.stats.get("model_digest") and run.stats["model_digest"] != digest
            ):
                raise RuntimeError("ollama_model_changed")
            run.stats = {**run.stats, "model_digest": digest, "provider": "ollama"}
            run.status = "running"
            run.heartbeat_at = datetime.now(UTC)
            await session.commit()
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            keys = await self._keys_with_status(run_id, {"pending", "submitted"})
            for key in keys:
                if stop.is_set():
                    await self._set_run_status(run_id, "cancelled")
                    return
                await self._classify_key(run_id, key)
            if await self._local_client.readiness() != digest:
                raise RuntimeError("ollama_model_changed")
            await self._validate_run(run_id)
            if stop.is_set():
                await self._set_run_status(run_id, "cancelled")
                return
            await self.apply_run(run_id, market_lock=market_lock)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def cancel_provider_work(self, run_id: int, client: Any) -> None:
        await self._set_run_status(run_id, "cancelled")

    async def _heartbeat(self, run_id: int) -> None:
        while True:
            await asyncio.sleep(2)
            async with self._sessions() as session:
                await session.execute(
                    update(AiGroupingRun)
                    .where(
                        AiGroupingRun.id == run_id,
                        AiGroupingRun.status.in_(("running", "validating", "waiting_for_market")),
                    )
                    .values(heartbeat_at=datetime.now(UTC))
                )
                await session.commit()

    async def _classify_key(self, run_id: int, key: str) -> None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(Listing, Brand)
                    .join(Brand, Brand.id == Listing.brand_id)
                    .join(AiGroupingItem, AiGroupingItem.listing_id == Listing.id)
                    .where(AiGroupingItem.run_id == run_id, AiGroupingItem.request_key == key)
                    .order_by(Listing.id)
                    .limit(1)
                )
            ).first()
        if row is None:
            raise RuntimeError("grouping_input_missing")
        listing, brand = row
        kind = self._product_type(listing, brand.name)
        decision = LocalDecision(
            product_type=kind, model_span=None, unclear=True, reason="insufficient_evidence"
        )
        accepted = False
        in_tokens = out_tokens = 0
        if kind is not None and len(listing.title) <= 300:
            data = {
                "brand": brand.name,
                "category": listing.category,
                "subcategory": listing.subcategory,
                "title": listing.title,
                "locked_product_type": kind,
            }
            decision, ins, outs = await self._local_client.classify(data)
            in_tokens += ins
            out_tokens += outs
            if self._valid(decision, listing.title, brand.name, kind):
                check, ins, outs = await self._local_client.classify(data, verify=True)
                in_tokens += ins
                out_tokens += outs
                accepted = self._valid(check, listing.title, brand.name, kind) and (
                    canonical_model(decision.model_span or "", brand.name)
                    == canonical_model(check.model_span or "", brand.name)
                )
        normalized = canonical_model(decision.model_span or "", brand.name) if accepted else None
        async with self._sessions() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(AiGroupingItem)
                .where(
                    AiGroupingItem.run_id == run_id,
                    AiGroupingItem.request_key == key,
                )
                .values(
                    status="classified" if accepted else "ambiguous",
                    product_type=kind,
                    model_span=decision.model_span if accepted else None,
                    normalized_model=normalized,
                    confidence=Decimal(0),
                    is_ambiguous=not accepted,
                    result={
                        "candidate_id": None,
                        "evidence": decision.model_span if accepted else None,
                        "reason": "verified_evidence"
                        if accepted
                        else "insufficient_or_conflicting_evidence",
                        "two_pass_agreement": accepted,
                    },
                    updated_at=now,
                )
            )
            run = await session.get(AiGroupingRun, run_id)
            assert run is not None
            run.input_tokens += in_tokens
            run.output_tokens += out_tokens
            counts: dict[str, int] = dict(
                (
                    await session.execute(
                        select(AiGroupingItem.status, func.count())
                        .where(AiGroupingItem.run_id == run_id)
                        .group_by(AiGroupingItem.status)
                    )
                )
                .tuples()
                .all()
            )
            run.completed_items = counts.get("classified", 0)
            run.ambiguous_items = counts.get("ambiguous", 0)
            run.heartbeat_at = now
            await session.commit()

    @staticmethod
    def _valid(decision: LocalDecision, title: str, brand: str, kind: str) -> bool:
        return (
            not decision.unclear
            and decision.reason == "explicit_model"
            and decision.product_type == kind
            and decision.model_span is not None
            and model_evidence_valid(title, decision.model_span, brand, kind)
        )
