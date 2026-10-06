"""Source-independent safety and local protocol contracts, not parser acceptance."""

from __future__ import annotations

import importlib
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.models import AiGroupingBatch, AiGroupingRun, ListingModelAssignment, ModelGroup
from app.services.ai_grouping.domain import stable_ai_key
from app.services.ai_grouping.local_client import LocalDecision, LocalGroupingClient
from app.services.ai_grouping.local_service import LocalGroupingService
from app.services.ai_grouping.safety import canonical_model, model_evidence_valid, product_type


@pytest.mark.parametrize(
    ("title", "subcategory", "expected"),
    [
        ("Chrome Hearts Dagger Pendant", "jewelry_watches", "necklace"),
        ("Chrome Hearts Dagger Hat", "hats", "hat"),
        ("Chrome Hearts Dagger Hat", "necklaces", None),
        ("Chrome Hearts Dagger Pendant", "hats", None),
        ("Chrome Hearts Dagger", "jewelry_watches", None),
        ("Chrome Hearts Dagger Pendant Hat", "hats", None),
    ],
)
def test_physical_type_conflicts_abstain(
    title: str, subcategory: str, expected: str | None
) -> None:
    assert product_type(title, "Chrome Hearts", "accessories", subcategory) == expected


def test_geobasket_colors_group_but_hats_cannot_join_pendants() -> None:
    assert canonical_model("Geobaskets Milk", "Rick Owens") == "geobasket"
    assert canonical_model("Geobasket", "Rick Owens") == "geobasket"
    assert product_type("Geobasket Milk", "Rick Owens", "footwear", "sneakers") == "footwear"
    assert product_type("Geobasket Milk", "Rick Owens", "accessories", "hats") is None
    assert stable_ai_key("Chrome Hearts", "hat", "Dagger") != stable_ai_key(
        "Chrome Hearts", "necklace", "Dagger"
    )


@pytest.mark.parametrize(
    ("title", "span", "valid"),
    [
        ("Double Dagger Pendant", "Dagger", False),
        ("Double Dagger Pendant", "Double Dagger", True),
        ("Dagger Dog Tag Pendant", "Dagger", False),
        ("Dagger #5 Pendant", "Dagger", False),
        ("Dagger #5 Pendant", "Dagger #5", True),
        ("Dagger Pendant", "Imaginary", False),
        ("Logo Hat", "Logo", False),
    ],
)
def test_distinguishing_evidence_is_required(title: str, span: str, valid: bool) -> None:
    assert model_evidence_valid(title, span, "Chrome Hearts", "necklace") is valid


def test_model_claim_cannot_override_local_type() -> None:
    result = LocalDecision(
        product_type="necklace", model_span="Dagger", unclear=False, reason="explicit_model"
    )
    assert not LocalGroupingService._valid(result, "Dagger Hat", "Chrome Hearts", "hat")


@pytest.mark.parametrize(
    "invalid",
    [
        '{"product_type":"hat","model_span":"Dagger","unclear":"false","reason":"explicit_model"}',
        '{"product_type":"hat","model_span":"Dagger","unclear":false}',
        "not json",
    ],
)
async def test_malformed_local_output_abstains(invalid: str) -> None:
    client = LocalGroupingClient(Settings())
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"done": True, "done_reason": "stop", "message": {"content": invalid}}
            )
        ),
    )
    result, _, _ = await client.classify({"title": "Dagger Hat"})
    assert result.unclear and result.model_span is None
    await client.close()


class _ProtocolClient(LocalGroupingClient):
    """Narrow LLM protocol double for persistence checks; never a Grailed source."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_at: int | None = None
        self.digest = "fixed-model-digest"

    async def readiness(self) -> str:
        return self.digest

    async def classify(self, data, *, verify=False):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("ollama_request_failed")
        return (
            LocalDecision(
                product_type=data["locked_product_type"],
                model_span="Cross",
                unclear=False,
                reason="explicit_model",
            ),
            100,
            20,
        )


async def test_local_resume_retains_progress_and_atomic_rollback(tmp_path):  # type: ignore[no-untyped-def]
    # Reuse the existing source-independent DB contract setup, not parser acceptance data.
    contracts = importlib.import_module("test_ai_grouping_service")
    engine, factory, settings = await contracts._database(tmp_path)
    await contracts._seed(factory)
    client = _ProtocolClient()
    service = LocalGroupingService(factory, settings, client)
    run_id = await service.create_run("canary", Decimal(0))
    client.fail_at = 3  # First unique input is fully persisted before the next request fails.
    with pytest.raises(RuntimeError, match="ollama_request_failed"):
        await service.process(run_id, client)
    assert len(await service._keys_with_status(run_id, {"pending"})) == 1
    client.fail_at = None
    await service.process(run_id, client)
    assert client.calls == 5  # The completed first pair was not recomputed.
    async with factory() as session:
        run = await session.get(AiGroupingRun, run_id)
        assert run is not None and run.status == "completed"
        assert run.actual_cost_usd == 0 and run.stats["model_digest"] == "fixed-model-digest"
        assignments = list(await session.scalars(select(ListingModelAssignment)))
        assert len(assignments) == 3
        assert all(item.method == "ollama_exact" for item in assignments)
        assert len({item.model_group_id for item in assignments}) == 2
        assert await session.scalar(select(func.count()).select_from(AiGroupingBatch)) == 0
        groups = list(
            await session.scalars(
                select(ModelGroup).where(ModelGroup.stable_key.like("local-v1:%"))
            )
        )
        assert len(groups) == 2
    assert (await service.preflight("pending"))["listing_count"] == 0
    client.digest = "changed-model-digest"
    changed = await service.preflight("remaining")
    assert changed["listing_count"] == 3
    assert changed["blocked_reason"] == "canary_required"
    await service.rollback_run(run_id)
    async with factory() as session:
        assignments = list(await session.scalars(select(ListingModelAssignment)))
        assert all(item.method == "exact_line" for item in assignments)
        assert len({item.model_group_id for item in assignments}) == 1
    await engine.dispose()


async def test_local_request_has_fixed_loopback_and_bounded_generation() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length",
                "message": {
                    "content": json.dumps(
                        {
                            "product_type": "hat",
                            "model_span": "Dagger",
                            "unclear": False,
                            "reason": "explicit_model",
                        }
                    )
                },
            },
        )

    client = LocalGroupingClient(Settings())
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(handle)
    )
    decision, _, _ = await client.classify({"title": "Dagger Hat"})
    assert decision.unclear  # Even valid JSON cannot rescue a truncated generation.
    assert requests[0].url.host == "127.0.0.1"
    payload = json.loads(requests[0].content)
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["num_predict"] == 512
    assert payload["stream"] is False
    assert payload["format"]["additionalProperties"] is False
    await client.close()
