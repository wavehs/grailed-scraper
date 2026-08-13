"""In-memory per-run metrics with durable JSON snapshots."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean
from typing import Any


@dataclass(slots=True)
class RunMetrics:
    started_monotonic: float = field(default_factory=time.monotonic)
    elapsed_before_s: float = 0.0
    requests_by_tier: Counter[str] = field(default_factory=Counter)
    http_errors_by_code: Counter[str] = field(default_factory=Counter)
    retries: int = 0
    rate_limit_hits: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    hits_fetched: int = 0
    listings_inserted: int = 0
    listings_updated: int = 0
    listings_invalid: int = 0
    quality_flags_counts: Counter[str] = field(default_factory=Counter)
    coverage_by_brand: dict[str, str | None] = field(default_factory=dict)
    browser_restarts: int = 0
    proxy_failures: int = 0

    @classmethod
    def resume(cls, snapshot: dict[str, Any] | None) -> RunMetrics:
        metrics = cls()
        if not snapshot:
            return metrics
        metrics.requests_by_tier.update(_dict(snapshot.get("requests_by_tier")))
        metrics.http_errors_by_code.update(_dict(snapshot.get("http_errors_by_code")))
        for key in (
            "retries", "rate_limit_hits", "cache_hits", "cache_misses",
            "hits_fetched", "listings_inserted", "listings_updated",
            "listings_invalid", "browser_restarts", "proxy_failures",
        ):
            setattr(metrics, key, int(snapshot.get(key, 0)))
        metrics.quality_flags_counts.update(_dict(snapshot.get("quality_flags_counts")))
        samples = snapshot.get("_latency_samples_ms", [])
        if isinstance(samples, list):
            metrics.latencies_ms = [float(value) for value in samples]
        metrics.elapsed_before_s = float(snapshot.get("duration_s", 0.0))
        coverage = snapshot.get("coverage_by_brand", {})
        if isinstance(coverage, dict):
            metrics.coverage_by_brand = {
                str(k): None if v is None else str(v) for k, v in coverage.items()
            }
        return metrics

    def record_response(self, tier: str, status_code: int, duration_ms: float) -> None:
        self.requests_by_tier[tier] += 1
        self.latencies_ms.append(duration_ms)
        if status_code >= 400:
            self.http_errors_by_code[str(status_code)] += 1
        if status_code == 429:
            self.rate_limit_hits += 1

    def record_cache(self, hit: bool) -> None:
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_listings(
        self,
        *,
        fetched: int = 0,
        inserted: int = 0,
        updated: int = 0,
        invalid: int = 0,
    ) -> None:
        self.hits_fetched += fetched
        self.listings_inserted += inserted
        self.listings_updated += updated
        self.listings_invalid += invalid

    def record_quality_flags(self, flags: list[str]) -> None:
        self.quality_flags_counts.update(flags)

    def snapshot(self, *, duration_s: float | None = None) -> dict[str, Any]:
        latencies = sorted(self.latencies_ms)
        p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
        cache_total = self.cache_hits + self.cache_misses
        return {
            "requests_total": sum(self.requests_by_tier.values()),
            "requests_by_tier": dict(self.requests_by_tier),
            "http_errors_by_code": dict(self.http_errors_by_code),
            "retries": self.retries,
            "rate_limit_hits": self.rate_limit_hits,
            "avg_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
            "p95_latency_ms": round(latencies[p95_index], 2) if latencies else 0.0,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": round(self.cache_hits / cache_total, 5) if cache_total else 0.0,
            "hits_fetched": self.hits_fetched,
            "listings_inserted": self.listings_inserted,
            "listings_updated": self.listings_updated,
            "listings_invalid": self.listings_invalid,
            "quality_flags_counts": dict(self.quality_flags_counts),
            "coverage_by_brand": dict(self.coverage_by_brand),
            "browser_restarts": self.browser_restarts,
            "proxy_failures": self.proxy_failures,
            "_latency_samples_ms": latencies,
            "duration_s": round(
                duration_s
                if duration_s is not None
                else self.elapsed_before_s + time.monotonic() - self.started_monotonic,
                3,
            ),
        }


def _dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item) for key, item in value.items()}
