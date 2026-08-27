"""Minimal REST client for Gemini Batch without persisting provider payloads."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

_BASE_URL = "https://generativelanguage.googleapis.com"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    "BATCH_STATE_SUCCEEDED",
    "BATCH_STATE_FAILED",
    "BATCH_STATE_CANCELLED",
    "BATCH_STATE_EXPIRED",
}
_BATCH_NAME = re.compile(r"batches/[A-Za-z0-9._~-]+")


class GeminiApiError(RuntimeError):
    """Sanitized provider failure safe for logs and API responses."""

    def __init__(self, status_code: int, *, retryable: bool) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"gemini_http_{status_code}")


@dataclass(frozen=True, slots=True)
class ProviderBatch:
    name: str
    display_name: str
    state: str
    done: bool
    responses: list[dict[str, Any]]


class GeminiBatchClient:
    """Call the small Batch REST surface used by the grouping pipeline."""

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("gemini_api_key_missing")
        self._api_key = api_key
        self._http = http_client or httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self._owns_http = http_client is None
        self._sleeper = sleeper
        self._max_attempts = max_attempts

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def create_batch(
        self,
        *,
        model: str,
        display_name: str,
        requests: Sequence[tuple[str, dict[str, Any]]],
    ) -> ProviderBatch:
        payload = {
            "batch": {
                "display_name": display_name,
                "input_config": {
                    "requests": {
                        "requests": [
                            {"request": request, "metadata": {"key": key}}
                            for key, request in requests
                        ]
                    }
                },
            }
        }
        response = await self._request(
            "POST",
            f"/v1beta/models/{model}:batchGenerateContent",
            retry=False,
            json=payload,
        )
        batch = _provider_batch(response)
        if not batch.name:
            raise GeminiApiError(502, retryable=False)
        return batch

    async def get_batch(self, name: str) -> ProviderBatch:
        response = await self._request("GET", f"/v1beta/{_batch_name(name)}")
        return _provider_batch(response)

    async def cancel_batch(self, name: str) -> None:
        await self._request("POST", f"/v1beta/{_batch_name(name)}:cancel", retry=False)

    async def list_batches(self) -> list[ProviderBatch]:
        result: list[ProviderBatch] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if page_token is not None:
                params["pageToken"] = page_token
            response = await self._request("GET", "/v1beta/batches", params=params)
            values = response.get("batches")
            if isinstance(values, list):
                result.extend(
                    batch
                    for item in values
                    if isinstance(item, dict)
                    if (batch := _provider_batch(item)).name
                )
            next_token = str(response.get("nextPageToken") or "")
            if not next_token:
                return result
            if next_token in seen_tokens:
                raise GeminiApiError(502, retryable=False)
            seen_tokens.add(next_token)
            page_token = next_token

    async def _request(
        self, method: str, path: str, *, retry: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        attempts = self._max_attempts if retry else 1
        for attempt in range(attempts):
            try:
                response = await self._http.request(
                    method, f"{_BASE_URL}{path}", headers=headers, **kwargs
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                if attempt + 1 == attempts:
                    raise GeminiApiError(0, retryable=True) from exc
                await self._sleeper(float(2**attempt))
                continue
            if response.is_success:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise GeminiApiError(502, retryable=False) from exc
                return payload if isinstance(payload, dict) else {}
            retryable = response.status_code in _RETRYABLE_STATUS
            if not retryable or attempt + 1 == attempts:
                raise GeminiApiError(response.status_code, retryable=retryable)
            await self._sleeper(float(2**attempt))
        raise GeminiApiError(0, retryable=True)


def _batch_name(value: str) -> str:
    normalized = value.strip().removeprefix("/v1beta/")
    if _BATCH_NAME.fullmatch(normalized) is None:
        raise ValueError("invalid_gemini_batch_name")
    return normalized


def _provider_batch(payload: dict[str, Any]) -> ProviderBatch:
    raw_metadata = payload.get("metadata")
    raw_response = payload.get("response")
    raw_dest = payload.get("dest")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    response: dict[str, Any] = raw_response if isinstance(raw_response, dict) else {}
    dest: dict[str, Any] = raw_dest if isinstance(raw_dest, dict) else {}
    raw_output = metadata.get("output")
    output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
    values: Any = (
        response.get("inlinedResponses")
        or response.get("inlined_responses")
        or dest.get("inlinedResponses")
        or dest.get("inlined_responses")
        or output.get("inlinedResponses")
        or output.get("inlined_responses")
        or []
    )
    if isinstance(values, dict):
        values = values.get("inlinedResponses") or values.get("inlined_responses") or []
    state = str(payload.get("state") or metadata.get("state") or "JOB_STATE_PENDING")
    state = state.replace("BATCH_STATE_", "JOB_STATE_", 1)
    raw_name = str(payload.get("name") or metadata.get("name") or "")
    return ProviderBatch(
        name=_batch_name(raw_name) if raw_name else "",
        display_name=str(
            payload.get("displayName")
            or payload.get("display_name")
            or metadata.get("displayName")
            or metadata.get("display_name")
            or ""
        ),
        state=state,
        done=bool(payload.get("done")) or state in _TERMINAL_STATES,
        responses=[item for item in values if isinstance(item, dict)],
    )
