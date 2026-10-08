"""What a model group stands for: a model, a description, a collaboration or no model."""

from __future__ import annotations

from typing import Literal

from app.services.grouping.collabs import COLLAB_PREFIX
from app.services.grouping.descriptors import DESCRIPTOR_PREFIX
from app.services.grouping.policy import REVIEW_TYPE

NONE_SLUG = "_none"

__all__ = [
    "COLLAB_PREFIX",
    "NONE_SLUG",
    "GroupKind",
    "collab_partner",
    "group_kind",
    "is_collab_slug",
    "is_descriptor_slug",
    "is_service_slug",
]

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


def is_descriptor_slug(slug: str) -> bool:
    return slug.startswith(DESCRIPTOR_PREFIX)


def is_collab_slug(slug: str) -> bool:
    return slug.startswith(COLLAB_PREFIX)


def is_service_slug(slug: str) -> bool:
    """"No model", a description or a collaboration line: made by the system, never a model."""

    return slug == NONE_SLUG or is_descriptor_slug(slug) or is_collab_slug(slug)


def collab_partner(slug: str) -> str | None:
    """``_collab-yeezy-gap`` -> ``yeezy-gap``; other slugs have no partner."""

    return slug[len(COLLAB_PREFIX) :] if slug.startswith(COLLAB_PREFIX) else None
