from __future__ import annotations

import pytest

from app.api.cursors import decode_cursor, encode_cursor
from app.api.errors import ApiError


def test_cursor_round_trip_and_request_binding() -> None:
    parameters = {"search": "boots", "status": "active", "limit": 50}
    token = encode_cursor(
        "catalog",
        position={"id": 42},
        context={"max_id": 100},
        parameters=parameters,
    )

    payload = decode_cursor(token, "catalog", parameters=parameters)
    assert payload.position == {"id": 42}
    assert payload.context == {"max_id": 100}

    with pytest.raises(ApiError) as error:
        decode_cursor(token, "catalog", parameters={**parameters, "status": "sold"})
    assert error.value.status_code == 422

    with pytest.raises(ApiError):
        decode_cursor(token + "!", "catalog", parameters=parameters)
