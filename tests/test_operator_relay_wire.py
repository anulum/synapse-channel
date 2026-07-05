# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — wire codec for relaying a governed operator action to a peer hub

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
    decode_relay_request,
    decode_relay_result,
    encode_relay_request,
    encode_relay_result,
)


def _request(**overrides: Any) -> RelayActionRequest:
    fields = {
        "action": "release",
        "namespace": "SYNAPSE-CHANNEL",
        "task_id": "t1",
        "operator": "ops-admin",
        "origin_hub_id": "syn-a",
    }
    fields.update(overrides)
    return RelayActionRequest(**fields)  # type: ignore[arg-type]


def _result(**overrides: Any) -> RelayActionResult:
    fields: dict[str, Any] = {
        "applied": True,
        "action": "release",
        "namespace": "SYNAPSE-CHANNEL",
        "task_id": "t1",
        "owner_hub_id": "syn-b",
        "detail": "released",
    }
    fields.update(overrides)
    return RelayActionResult(**fields)


# --- round trips -------------------------------------------------------------------------


def test_request_round_trips() -> None:
    request = _request()
    assert decode_relay_request(encode_relay_request(request)) == request


def test_result_round_trips() -> None:
    result = _result()
    assert decode_relay_result(encode_relay_result(result)) == result


def test_result_defaults_absent_detail_to_empty() -> None:
    body = encode_relay_result(_result(detail=""))
    del body["detail"]
    assert decode_relay_result(body).detail == ""


def test_refused_result_round_trips() -> None:
    result = _result(applied=False, detail="scope_not_granted")
    assert decode_relay_result(encode_relay_result(result)) == result


# --- encode fails closed on an empty identifier ------------------------------------------


@pytest.mark.parametrize("field", ["action", "namespace", "task_id", "operator", "origin_hub_id"])
def test_encode_request_rejects_an_empty_identifier(field: str) -> None:
    with pytest.raises(RelayWireError, match=field):
        encode_relay_request(_request(**{field: "  "}))


@pytest.mark.parametrize("field", ["action", "namespace", "task_id", "owner_hub_id"])
def test_encode_result_rejects_an_empty_identifier(field: str) -> None:
    with pytest.raises(RelayWireError, match=field):
        encode_relay_result(_result(**{field: ""}))


# --- decode is defensive at every field --------------------------------------------------


def test_decode_request_rejects_a_non_object() -> None:
    with pytest.raises(RelayWireError, match="request body must be a JSON object"):
        decode_relay_request([1, 2, 3])


def test_decode_request_rejects_a_missing_field() -> None:
    body = encode_relay_request(_request())
    del body["operator"]
    with pytest.raises(RelayWireError, match="operator"):
        decode_relay_request(body)


def test_decode_request_rejects_a_non_string_field() -> None:
    body = encode_relay_request(_request())
    body["task_id"] = 7
    with pytest.raises(RelayWireError, match="task_id must be a string"):
        decode_relay_request(body)


def test_decode_result_rejects_a_non_object() -> None:
    with pytest.raises(RelayWireError, match="result body must be a JSON object"):
        decode_relay_result("not-a-mapping")


def test_decode_result_rejects_a_non_boolean_applied() -> None:
    body = encode_relay_result(_result())
    body["applied"] = "yes"
    with pytest.raises(RelayWireError, match="applied must be a boolean"):
        decode_relay_result(body)


def test_decode_result_rejects_a_non_string_detail() -> None:
    body = encode_relay_result(_result())
    body["detail"] = 5
    with pytest.raises(RelayWireError, match="detail must be a string"):
        decode_relay_result(body)


def test_decode_result_rejects_a_missing_identifier() -> None:
    body = encode_relay_result(_result())
    del body["owner_hub_id"]
    with pytest.raises(RelayWireError, match="owner_hub_id"):
        decode_relay_result(body)
