"""Seller-identity minimization and local salt management."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings

_WHITESPACE = re.compile(r"\s+")


def seller_identity(value: object, settings: Settings, *, root: Path = PROJECT_ROOT) -> str | None:
    """Return the configured privacy-preserving seller identifier."""

    username = _WHITESPACE.sub(" ", str(value)).strip().casefold()
    if not username or settings.store_seller_identity == "none":
        return None
    if settings.store_seller_identity == "plain":
        return username
    salt = settings.seller_identity_salt or _local_salt(root)
    return hashlib.sha256(f"{username}\0{salt}".encode()).hexdigest()


def _local_salt(root: Path) -> str:
    path = root / "data" / "secrets" / "seller_identity_salt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)
    return value


def compliance_reasons(settings: Settings) -> list[str]:
    reasons: list[str] = []
    if settings.source_mode == "live" and not settings.live_compliance_acknowledged:
        reasons.append("live_compliance_not_acknowledged")
    if settings.store_seller_identity == "plain":
        reasons.append("seller_identity_plaintext_enabled")
    return reasons


def require_live_compliance(settings: Settings) -> None:
    if settings.source_mode == "live" and not settings.live_compliance_acknowledged:
        raise RuntimeError("live_compliance_not_acknowledged")
