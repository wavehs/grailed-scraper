"""What a model group stands for: a model, a description, a collaboration or no model."""

from __future__ import annotations

from typing import Literal

from app.services.grouping.policy import REVIEW_TYPE

NONE_SLUG = "_none"
# Service groups keep a leading underscore so mined phrases can never collide with them.
COLLAB_PREFIX = "_collab-"
DESCRIPTOR_PREFIX = "_desc-"

GroupKind = Literal["model", "descriptor", "collab", "none", "review"]


def group_kind(slug: str, product_type: str) -> GroupKind:
    if product_type == REVIEW_TYPE:
        return "review"
    if slug == NONE_SLUG:
        return "none"
    if slug.startswith(COLLAB_PREFIX):
        return "collab"
    if slug.startswith(DESCRIPTOR_PREFIX):
        return "descriptor"
    return "model"


def collab_partner(slug: str) -> str | None:
    """``_collab-yeezy-gap`` -> ``yeezy-gap``; other slugs have no partner."""

    return slug[len(COLLAB_PREFIX) :] if slug.startswith(COLLAB_PREFIX) else None
