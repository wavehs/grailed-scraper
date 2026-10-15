"""The stored Algolia key of Grailed, and a refresh that rediscovers it when it expires."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import SourceCredential
from app.services.sources.grailed.algolia.models import AlgoliaCredentialsData
from app.services.sources.grailed.discovery.service import DiscoveryService
from app.services.transport.protocols import HttpTransport


async def stored_credential(session: AsyncSession) -> SourceCredential | None:
    rows = await session.scalars(
        select(SourceCredential).where(SourceCredential.source == "grailed")
    )
    return rows.first()


def as_algolia(credential: SourceCredential) -> AlgoliaCredentialsData:
    return AlgoliaCredentialsData(credential.app_id, credential.api_key, credential.algolia_agent)


def credential_refresher(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, transport: HttpTransport
) -> Callable[[], Awaitable[AlgoliaCredentialsData]]:
    """What ``AlgoliaClient`` calls on 401/403: rediscover the key and reload it."""

    async def refresh() -> AlgoliaCredentialsData:
        async with sessions() as session:
            await DiscoveryService(session, settings, transport).invalidate_and_refresh()
            refreshed = await stored_credential(session)
            if refreshed is None:
                raise RuntimeError("discovery_required")
            return as_algolia(refreshed)

    return refresh
