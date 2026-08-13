"""Passive capture of matching Algolia responses produced by Grailed itself."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any
from urllib.parse import quote

from app.services.sources.grailed.algolia.exceptions import AlgoliaTransient
from app.services.transport.protocols import BrowserPage


class PassiveAlgoliaInterceptor:
    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._timeout_s = timeout_s

    async def capture(
        self,
        page: BrowserPage,
        *,
        navigation_url: str,
        index_name: str,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[dict[str, Any]] = loop.create_future()
        expected = quote(index_name, safe="")
        expected_fingerprint = _fingerprint(request_body) if request_body is not None else None

        async def inspect_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "algolia" not in url or (index_name != "*" and expected not in url):
                return
            observed_body = getattr(getattr(response, "request", None), "post_data", None)
            if (
                expected_fingerprint is not None
                and isinstance(observed_body, str)
                and _fingerprint_text(observed_body) != expected_fingerprint
            ):
                return
            try:
                payload = response.json()
                if inspect.isawaitable(payload):
                    payload = await payload
            except Exception:
                return
            if isinstance(payload, dict) and not result.done():
                result.set_result(payload)

        def on_response(response: Any) -> None:
            loop.create_task(inspect_response(response))

        page.on("response", on_response)
        try:
            await page.goto(
                navigation_url,
                wait_until="domcontentloaded",
                timeout=self._timeout_s * 1_000,
            )
            return await asyncio.wait_for(result, timeout=self._timeout_s)
        except TimeoutError as exc:
            raise AlgoliaTransient("browser interception") from exc
        finally:
            off = getattr(page, "off", None)
            if off is not None:
                off("response", on_response)


def _fingerprint(body: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fingerprint_text(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return ""
    return _fingerprint(parsed) if isinstance(parsed, dict) else ""
