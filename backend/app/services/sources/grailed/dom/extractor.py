"""Scrapling-only adaptive extraction of Grailed-shaped raw hits."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from scrapling import Selector

from app.services.sources.base.models import RawHit

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


class DomExtractor:
    def extract(self, html: str, *, url: str = "https://www.grailed.com") -> tuple[RawHit, ...]:
        page = Selector(html, url=url, adaptive=True)
        embedded = self._embedded(page)
        if embedded:
            return tuple(RawHit(hit, "T3") for hit in embedded)
        cards = list(
            page.css(
                '[data-testid="listing-item"], [data-listing-id], article.listing-card',
                adaptive=True,
                auto_save=True,
            )
        )
        if len(cards) == 1:
            cards.extend(item for item in cards[0].find_similar() if item not in cards)
        hits = [hit for card in cards if (hit := self._card(card, url)) is not None]
        if hits:
            return tuple(RawHit(hit, "T3") for hit in hits)
        detail = self._detail(page, url)
        return (RawHit(detail, "T3"),) if detail is not None else ()

    def _embedded(self, page: Selector) -> tuple[dict[str, Any], ...]:
        documents: list[Any] = []
        selectors = (
            "script#__NEXT_DATA__::text",
            'script[type="application/ld+json"]::text',
        )
        for selector in selectors:
            for text in page.css(selector).getall():
                try:
                    documents.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
        documents.extend(self._preloaded(page.get()))
        hits: list[dict[str, Any]] = []
        for document in documents:
            hits.extend(self._find_hits(document))
        unique: dict[str, dict[str, Any]] = {}
        for hit in hits:
            object_id = hit.get("objectID", hit.get("id"))
            if object_id is not None:
                unique[str(object_id)] = hit
        return tuple(unique.values())

    @staticmethod
    def _preloaded(html: str) -> list[Any]:
        match = re.search(
            r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>",
            html,
            flags=re.DOTALL,
        )
        if match is None:
            return []
        try:
            return [json.loads(match.group(1))]
        except json.JSONDecodeError:
            return []

    def _find_hits(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            direct = [item for item in value if isinstance(item, dict)]
            if direct and any("id" in item or "objectID" in item for item in direct):
                return [self._json_ld(item) for item in direct]
            return [hit for item in value for hit in self._find_hits(item)]
        if not isinstance(value, dict):
            return []
        if value.get("@type") == "Product":
            return [self._json_ld(value)]
        if "id" in value or "objectID" in value:
            return [self._json_ld(value)]
        return [hit for item in value.values() for hit in self._find_hits(item)]

    @staticmethod
    def _json_ld(value: dict[str, Any]) -> dict[str, Any]:
        if value.get("@type") != "Product":
            return dict(value)
        raw_offers = value.get("offers")
        offers: dict[str, Any] = raw_offers if isinstance(raw_offers, dict) else {}
        raw_brand = value.get("brand")
        brand: dict[str, Any] = raw_brand if isinstance(raw_brand, dict) else {}
        identifier = value.get("sku", value.get("productID", value.get("id")))
        identifier_text = str(identifier)
        hit: dict[str, Any] = {
            "objectID": identifier_text,
            "id": int(identifier_text) if identifier_text.isdecimal() else identifier,
            "title": value.get("name", ""),
            "currency": offers.get("priceCurrency", "USD"),
            "designers": [{"name": brand.get("name", "")}],
            "url": value.get("url", ""),
        }
        try:
            hit["price_i"] = int(Decimal(str(offers.get("price"))) * 100)
        except (InvalidOperation, TypeError):
            pass
        return hit

    def _card(self, card: Selector, base_url: str) -> dict[str, Any] | None:
        raw_id = card.attrib.get("data-listing-id") or card.attrib.get("data-id")
        href = _first(card.css("a::attr(href)").getall())
        if raw_id is None and href:
            match = re.search(r"/listings/(\d+)", href)
            raw_id = match.group(1) if match else None
        if raw_id is None:
            return None
        title = _first(card.css("a::text").getall()) or card.get_all_text().strip()
        price_attr = _first(card.css("[data-price-cents]::attr(data-price-cents)").getall())
        hit: dict[str, Any] = {
            "objectID": str(raw_id),
            "id": int(raw_id) if str(raw_id).isdecimal() else raw_id,
            "title": title,
            "url": urljoin(base_url, href or f"/listings/{raw_id}"),
        }
        price = _price_cents(price_attr, card.get_all_text())
        if price is not None:
            hit["price_i"] = price
        designer = _first(card.css(".designer::text, [data-designer]::text").getall())
        if designer:
            hit["designers"] = [{"name": designer}]
        return hit

    def _detail(self, page: Selector, base_url: str) -> dict[str, Any] | None:
        roots = page.css('[data-testid="listing-detail"], main[data-listing-id]', adaptive=True)
        if not roots:
            return None
        return self._card(roots[0], base_url)


def _first(values: Iterable[str]) -> str | None:
    return next((value.strip() for value in values if value.strip()), None)


def _price_cents(attribute: str | None, text: str) -> int | None:
    if attribute and attribute.isdecimal():
        return int(attribute)
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    try:
        return int(Decimal(match.group(1).replace(",", "")) * 100)
    except InvalidOperation:
        return None
