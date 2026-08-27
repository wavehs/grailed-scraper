"""Gemini Batch transport contracts; no live request is made here."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai_grouping.client import GeminiApiError, GeminiBatchClient


async def test_batch_client_keeps_key_out_of_url_and_payload() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"name": "batches/123", "metadata": {"state": "JOB_STATE_PENDING"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = GeminiBatchClient("secret-value", http_client=http)
        result = await client.create_batch(
            model="gemini-2.5-flash-lite",
            display_name="ai-grouping-1-0",
            requests=[("req-1", {"contents": [{"parts": [{"text": "safe title"}]}]})],
        )

    assert result.name == "batches/123"
    assert result.display_name == ""
    assert result.state == "JOB_STATE_PENDING"
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["x-goog-api-key"] == "secret-value"
    assert "secret-value" not in str(request.url)
    body = json.loads(request.content)
    assert "secret-value" not in json.dumps(body)
    entry = body["batch"]["input_config"]["requests"]["requests"][0]
    assert entry["metadata"] == {"key": "req-1"}


async def test_batch_client_reads_state_results_and_cancels() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "name": "batches/123",
                "done": True,
                "metadata": {"state": "JOB_STATE_SUCCEEDED"},
                "response": {"inlinedResponses": [{"metadata": {"key": "req-1"}}]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GeminiBatchClient("secret", http_client=http)
        status = await client.get_batch("batches/123")
        await client.cancel_batch("batches/123")

    assert status.state == "JOB_STATE_SUCCEEDED"
    assert status.done is True
    assert status.responses == [{"metadata": {"key": "req-1"}}]
    assert calls == ["GET /v1beta/batches/123", "POST /v1beta/batches/123:cancel"]


async def test_batch_client_reads_current_operation_envelope() -> None:
    payload = {
        "name": "batches/123",
        "done": True,
        "metadata": {
            "displayName": "current-batch",
            "state": "BATCH_STATE_SUCCEEDED",
            "output": {
                "inlinedResponses": {
                    "inlinedResponses": [{"metadata": {"key": "req-1"}}]
                }
            },
        },
        "response": {
            "inlinedResponses": {
                "inlinedResponses": [{"metadata": {"key": "req-1"}}]
            }
        },
    }

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    ) as http:
        result = await GeminiBatchClient("secret", http_client=http).get_batch("batches/123")

    assert result.display_name == "current-batch"
    assert result.state == "JOB_STATE_SUCCEEDED"
    assert result.done is True
    assert result.responses == [{"metadata": {"key": "req-1"}}]


async def test_batch_client_retries_only_transient_statuses() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": {"message": "do not log me"}})
        return httpx.Response(200, json={"name": "batches/ok", "state": "JOB_STATE_RUNNING"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GeminiBatchClient(
            "secret", http_client=http, sleeper=lambda delay: _record_delay(delays, delay)
        )
        result = await client.get_batch("batches/ok")

    assert result.state == "JOB_STATE_RUNNING"
    assert attempts == 2
    assert delays == [1.0]


async def test_batch_creation_is_never_retried_after_an_ambiguous_failure() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GeminiBatchClient("secret", http_client=http)
        with pytest.raises(GeminiApiError):
            await client.create_batch(
                model="gemini-2.5-flash-lite",
                display_name="one-charge-only",
                requests=[("req-1", {"contents": []})],
            )

    assert attempts == 1


async def test_batch_list_follows_all_pages() -> None:
    pages: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        pages.append(token)
        if token is None:
            return httpx.Response(
                200,
                json={"batches": [{"name": "batches/first"}], "nextPageToken": "next"},
            )
        return httpx.Response(200, json={"batches": [{"name": "batches/second"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GeminiBatchClient("secret", http_client=http)
        batches = await client.list_batches()

    assert pages == [None, "next"]
    assert [batch.name for batch in batches] == ["batches/first", "batches/second"]


async def test_batch_resource_name_rejects_path_or_query_injection() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)) as http:
        client = GeminiBatchClient("secret", http_client=http)
        with pytest.raises(ValueError, match="invalid_gemini_batch_name"):
            await client.get_batch("batches/../models/key?alt=json")


async def test_batch_client_fails_fast_without_leaking_provider_body() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            401,
            json={"error": {"message": "secret provider diagnostics"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GeminiBatchClient("secret", http_client=http)
        with pytest.raises(GeminiApiError) as captured:
            await client.get_batch("batches/nope")

    assert attempts == 1
    assert captured.value.status_code == 401
    assert "secret provider diagnostics" not in str(captured.value)
    assert "secret" not in str(captured.value)


async def _record_delay(values: list[float], delay: float) -> None:
    values.append(delay)
