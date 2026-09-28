# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec contract for hub-to-hub message forwarding
"""Both forward shapes arrive from another host, so every malformed field must refuse."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from synapse_channel.core.message_forward_wire import (
    MAX_BODY_BYTES,
    ForwardKind,
    MessageForwardRequest,
    MessageForwardResult,
    MessageForwardWireError,
    decode_message_forward_request,
    decode_message_forward_result,
    encode_message_forward_request,
    encode_message_forward_result,
)
from synapse_channel.core.protocol import MessageType, build_envelope


def _request_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "forward_id": "f-1",
        "kind": "chat",
        "sender_seat": "PROJ/alice",
        "target_seat": "PROJ/bob",
        "body": {"payload": "hello"},
    }
    fields.update(overrides)
    return fields


def _result_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "forward_id": "f-1",
        "disposition": "accepted",
        "answering_hub": "laptop",
        "reason_code": "",
        "detail": "",
        "result": {"seq": 7},
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize("kind", ["chat", "delivery_request"])
def test_targeted_request_round_trips_through_a_real_envelope(kind: str) -> None:
    """A request survives encoding into the hub envelope and JSON transport unchanged."""
    request = MessageForwardRequest(
        forward_id="f-1",
        kind=cast("ForwardKind", kind),
        sender_seat="PROJ/alice",
        target_seat="PROJ/bob",
        body={"payload": "hello", "nested": {"n": 1}},
    )
    envelope = build_envelope(
        "workstation",
        MessageType.MULTIHUB_MESSAGE_FORWARD,
        **encode_message_forward_request(request),
    )
    decoded = decode_message_forward_request(json.loads(json.dumps(envelope)))
    assert decoded == request


@pytest.mark.parametrize("kind", ["delivery_status", "delivery_cancel", "who"])
def test_untargeted_kinds_refuse_a_target_and_accept_none(kind: str) -> None:
    """Status, cancel and roster forwards address the hub, never a seat."""
    decoded = decode_message_forward_request(_request_fields(kind=kind, target_seat=""))
    assert decoded.target_seat == ""
    with pytest.raises(MessageForwardWireError, match="must not name"):
        decode_message_forward_request(_request_fields(kind=kind))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "broadcast"}, "unknown forward kind"),
        ({"target_seat": ""}, "requires target_seat"),
        ({"forward_id": ""}, "must not be empty"),
        ({"forward_id": "x" * 129}, "exceeds 128 bytes"),
        ({"forward_id": "a\tb"}, "control characters"),
        ({"forward_id": 7}, "must be a string"),
        ({"sender_seat": ""}, "must not be empty"),
        ({"sender_seat": "PROJ/alice@elsewhere"}, "hub-qualified"),
        ({"sender_seat": " PROJ/alice"}, "surrounding whitespace"),
        ({"target_seat": "PROJ/bob@third"}, "hub-qualified"),
        ({"target_seat": "b" * 257}, "exceeds 256 bytes"),
        ({"body": ["not", "an", "object"]}, "must be a JSON object"),
        ({"body": {"n": float("nan")}}, "not JSON-serialisable"),
        ({"body": {"blob": "x" * MAX_BODY_BYTES}}, f"exceeds {MAX_BODY_BYTES} bytes"),
        ({"sender_seat": "PROJ/\ud800"}, "not valid UTF-8"),
    ],
)
def test_malformed_request_fields_refuse(overrides: dict[str, Any], message: str) -> None:
    """Every field is validated before the receiving hub acts on it."""
    with pytest.raises(MessageForwardWireError, match=message):
        decode_message_forward_request(_request_fields(**overrides))


def test_request_that_is_not_an_object_refuses() -> None:
    """A JSON array or string is not a forward."""
    with pytest.raises(MessageForwardWireError, match="must be a JSON object"):
        decode_message_forward_request(["forward"])


def test_encoding_validates_before_it_reaches_the_wire() -> None:
    """The sending hub cannot emit a request the receiving hub would refuse as malformed."""
    with pytest.raises(MessageForwardWireError, match="hub-qualified"):
        encode_message_forward_request(
            MessageForwardRequest(
                forward_id="f-1", kind="chat", sender_seat="a@b", target_seat="PROJ/bob"
            )
        )


@pytest.mark.parametrize(
    "result",
    [
        MessageForwardResult(forward_id="f-1", disposition="accepted", answering_hub="laptop"),
        MessageForwardResult(
            forward_id="f-1",
            disposition="duplicate",
            answering_hub="laptop",
            detail="already accepted",
            result={"seq": 3},
        ),
        MessageForwardResult(
            forward_id="f-1",
            disposition="refused",
            answering_hub="laptop",
            reason_code="namespace_not_granted",
            detail="peer may not address PROJ",
        ),
    ],
)
def test_result_round_trips(result: MessageForwardResult) -> None:
    """Each disposition survives JSON transport with its detail and payload."""
    encoded = json.loads(json.dumps(encode_message_forward_result(result)))
    assert decode_message_forward_result(encoded) == result


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"disposition": "delivered"}, "unknown forward disposition"),
        ({"answering_hub": "bad hub"}, "invalid answering_hub"),
        ({"answering_hub": 5}, "invalid answering_hub"),
        ({"reason_code": "why"}, "only valid on a refusal"),
        ({"disposition": "refused", "reason_code": ""}, "lower-case reason_code"),
        ({"disposition": "refused", "reason_code": "Bad-Code"}, "lower-case reason_code"),
        ({"reason_code": 3}, "must be a string"),
        ({"detail": "d" * 513}, "exceeds 512 bytes"),
        ({"result": "ok"}, "must be a JSON object"),
    ],
)
def test_malformed_result_fields_refuse(overrides: dict[str, Any], message: str) -> None:
    """A sending hub never reports an outcome from a result it cannot fully read."""
    with pytest.raises(MessageForwardWireError, match=message):
        decode_message_forward_result(_result_fields(**overrides))


def test_result_encoding_refuses_a_refusal_without_a_code() -> None:
    """A receiving hub cannot emit a refusal the sender would be unable to classify."""
    with pytest.raises(MessageForwardWireError, match="lower-case reason_code"):
        encode_message_forward_result(
            MessageForwardResult(forward_id="f-1", disposition="refused", answering_hub="laptop")
        )
