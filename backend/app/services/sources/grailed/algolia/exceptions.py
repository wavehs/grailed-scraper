"""Typed, secret-safe Algolia failures used by tier escalation."""

from __future__ import annotations


class AlgoliaError(RuntimeError):
    """Base error containing only a safe operation name."""

    def __init__(self, operation: str, status_code: int | None = None) -> None:
        message = f"Algolia {operation} failed"
        if status_code is not None:
            message += f" with HTTP {status_code}"
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code


class AlgoliaBadQuery(AlgoliaError):
    pass


class AlgoliaAuthError(AlgoliaError):
    pass


class AlgoliaIndexNotFound(AlgoliaError):
    pass


class AlgoliaRateLimited(AlgoliaError):
    pass


class AlgoliaTransient(AlgoliaError):
    pass


class WafChallenge(AlgoliaError):
    pass
