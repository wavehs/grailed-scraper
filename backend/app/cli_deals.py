"""``deals-scan``: one deal scan from the command line, or an offline re-check (plan 4.1).

Run it while the app is stopped, or use the Deals page: the app's market lock does not
reach another process.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.settings import effective_settings
from app.core.config import Settings
from app.db.session import create_database_engine
from app.services.deals.candidates import find_deals, rules_from
from app.services.deals.scanner import network_scan
from app.services.metrics.fees import load_fees


async def deals_scan(base: Settings, *, offline: bool, since_days: int) -> dict[str, object]:
    """Scan Grailed for the brands in scope, or re-judge listings changed in ``since_days``."""

    engine = create_database_engine(base)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            settings = await effective_settings(session, base)
        if not offline:
            report = await network_scan(sessions, settings, asyncio.Lock())
            return {"mode": "scan", **report.as_dict()}
        async with sessions() as session:
            found = await find_deals(
                session,
                rules_from(settings),
                load_fees(),
                since=datetime.now(UTC) - timedelta(days=since_days),
                by="changed",
            )
            await session.commit()
        return {"mode": "offline", "since_days": since_days, **found.as_dict()}
    finally:
        await engine.dispose()
