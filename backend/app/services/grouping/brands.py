"""One title normalizer per brand, shared by regrouping and by manual edits (split)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, BrandSourceMap, Listing
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import GroupingPolicy


async def load_brand_terms(
    session: AsyncSession, brand: Brand, designers: Iterable[str] | None = None
) -> list[str]:
    """Names that identify the brand in a title: its name, aliases, confirmed Grailed names
    and every designer of its listings (collaborations).

    ``designers`` skips the query when the caller has already read the listings.
    """

    if designers is None:
        found: set[str] = set()
        for names in await session.scalars(
            select(Listing.designer_names).where(Listing.brand_id == brand.id)
        ):
            found.update(names or ())
        designers = sorted(found)
    mappings = await session.scalars(
        select(BrandSourceMap.source_designer_name).where(
            BrandSourceMap.brand_id == brand.id, BrandSourceMap.rejected_at.is_(None)
        )
    )
    return list(dict.fromkeys([brand.name, *brand.aliases, *mappings, *designers]))


def brand_normalizer(
    policy: GroupingPolicy, brand: Brand, terms: Iterable[str]
) -> TitleNormalizer:
    return TitleNormalizer(policy, brand_terms=terms, brand_slug=brand.slug)
