"""Exact foreign-exchange rate lookup."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FxRate


class FxRateProvider(Protocol):
    async def rate_to_usd(self, currency: str, rate_date: date) -> Decimal | None: ...


class DatabaseFxRateProvider:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rate_to_usd(self, currency: str, rate_date: date) -> Decimal | None:
        if currency.upper() == "USD":
            return Decimal(1)
        return cast(
            Decimal | None,
            await self._session.scalar(
                select(FxRate.rate_to_usd).where(
                    FxRate.currency == currency.upper(), FxRate.rate_date == rate_date
                )
            ),
        )


class StaticFxRateProvider:
    """Small deterministic provider for configured exchange rates."""

    def __init__(self, rates: dict[tuple[str, date], Decimal] | None = None) -> None:
        self._rates = rates or {}

    async def rate_to_usd(self, currency: str, rate_date: date) -> Decimal | None:
        normalized = currency.upper()
        return Decimal(1) if normalized == "USD" else self._rates.get((normalized, rate_date))
