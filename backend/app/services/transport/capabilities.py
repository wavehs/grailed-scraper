"""Runtime compatibility report without importing Camoufox outside browser code."""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass
from importlib import metadata


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    scrapling_version: str | None
    camoufox_version: str | None
    fetcher_session_available: bool
    stealthy_session_available: bool

    @property
    def t1_available(self) -> bool:
        return self.fetcher_session_available

    @property
    def t2_available(self) -> bool:
        """T2 is driven by Scrapling's supported StealthySession API.

        Scrapling 0.4.11 installs browser binaries through ``scrapling install``
        and does not expose Camoufox as a separately versioned Python package.
        """

        return self.stealthy_session_available

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.update(t1_available=self.t1_available, t2_available=self.t2_available)
        return data


def probe_capabilities() -> CapabilityReport:
    """Inspect optional APIs safely; absence enables the documented fallback."""

    fetcher = stealthy = False
    try:
        fetchers = importlib.import_module("scrapling.fetchers")
        fetcher = hasattr(fetchers, "FetcherSession")
        stealthy = hasattr(fetchers, "AsyncStealthySession")
    except ImportError:
        pass
    return CapabilityReport(
        scrapling_version=_version("scrapling"),
        camoufox_version=_version("camoufox"),
        fetcher_session_available=fetcher,
        stealthy_session_available=stealthy,
    )
