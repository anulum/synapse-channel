# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded version-three delivery contract tests
"""Exercise admission and lifecycle refusal at the public contract boundary."""

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.delivery_modes import (
    DeliveryLifecycle,
    DeliveryRefusal,
    parse_delivery_intent,
    select_delivery_mode,
)


def _request(**changes: Any) -> dict[str, Any]:
    """Return a valid inbound request with specific hostile fields replaced."""
    frame: dict[str, Any] = {
        "sender": "forged/sender",
        "type": "delivery_request",
        "target": "P/recipient",
        "protocol_version": 3,
        "request_id": "request-1",
        "idempotency_key": "idem-1",
        "target_incarnation": "a" * 64,
        "mode": "steer",
        "allowed_fallbacks": ["next_turn"],
        "task_id": "T-1",
        "body": "Apply the reviewed change.",
        "deadline": 160.0,
    }
    frame.update(changes)
    return frame


def _parse(**changes: Any) -> Any:
    """Parse a request using server-derived sender and hub identity."""
    return parse_delivery_intent(
        _request(**changes), sender="P/author", origin_hub="hub-1", now=100.0
    )


def test_sender_origin_digest_and_explicit_fallback() -> None:
    """A forged envelope sender cannot change provenance or mode selection."""
    intent = _parse()
    assert intent.sender == "P/author"
    assert intent.origin_hub == "hub-1"
    assert select_delivery_mode(intent, {"next_turn": "emulated"}) == ("next_turn", "emulated")
    assert select_delivery_mode(intent, {"steer": "native", "next_turn": "emulated"}) == (
        "steer",
        "native",
    )
    assert len(intent.operation_key) == 64
    assert len(intent.digest) == 64
    assert _parse(body="Different content").digest != intent.digest
    assert _parse(request_id="request-2").operation_key != intent.operation_key
    with pytest.raises(DeliveryRefusal, match="none of the allowed modes") as failure:
        select_delivery_mode(intent, {"interrupt": "native"})
    assert failure.value.code == "unsupported_mode"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"protocol_version": 2}, "unsupported_protocol"),
        ({"protocol_version": True}, "unsupported_protocol"),
        ({"mode": "shutdown"}, "unsupported_mode"),
        ({"mode": None}, "unsupported_mode"),
        ({"target": "all"}, "invalid_shape"),
        ({"target": "P/*"}, "invalid_shape"),
        ({"target": "P/role?"}, "invalid_shape"),
        ({"target": "P/[abc]"}, "invalid_shape"),
        ({"target": ["P/recipient"]}, "invalid_shape"),
        ({"target_incarnation": "A" * 64}, "invalid_shape"),
        ({"request_id": ""}, "invalid_shape"),
        ({"idempotency_key": "x" * 129}, "invalid_shape"),
        ({"request_id": "bad\nline"}, "invalid_shape"),
        ({"body": "é" * 4097}, "invalid_shape"),
        ({"body": ""}, "invalid_shape"),
        ({"allowed_fallbacks": "next_turn"}, "invalid_shape"),
        ({"allowed_fallbacks": ["next_turn"] * 4}, "invalid_shape"),
        ({"allowed_fallbacks": ["steer"]}, "invalid_shape"),
        ({"allowed_fallbacks": ["next_turn", "next_turn"]}, "invalid_shape"),
        ({"allowed_fallbacks": ["shutdown"]}, "unsupported_mode"),
        ({"deadline": None}, "invalid_shape"),
        ({"deadline": True}, "invalid_shape"),
        ({"deadline": float("nan")}, "invalid_shape"),
        ({"deadline": float("inf")}, "invalid_shape"),
        ({"deadline": 10**1000}, "invalid_shape"),
        ({"deadline": 100.0}, "deadline_expired"),
        ({"deadline": 86501.0}, "invalid_shape"),
        ({"unexpected_permission": True}, "invalid_shape"),
    ],
)
def test_malformed_or_unsafe_requests_fail_closed(change: dict[str, Any], code: str) -> None:
    """Shape confusion, oversized content, and expired intent never reach a recipient."""
    with pytest.raises(DeliveryRefusal) as failure:
        _parse(**change)
    assert failure.value.code == code


def test_modes_and_safe_defaults() -> None:
    """All four named modes parse, with no implicit fallback or task association."""
    for mode in ("interrupt", "steer", "follow_up", "next_turn"):
        intent = _parse(mode=mode, allowed_fallbacks=[], task_id="")
        assert intent.mode == mode
        assert intent.allowed_fallbacks == ()
        assert intent.task_id == ""


def test_cancellation_request_does_not_claim_execution_outcome() -> None:
    """Completion may win a cancellation race without erasing the request fact."""
    accepted = DeliveryLifecycle("accepted")
    queued = accepted.advance("queued")
    boundary = queued.advance("boundary_delivered")
    acknowledged = boundary.advance("acknowledged")
    requested = acknowledged.request_cancel()
    assert requested.stage == "acknowledged"
    completed = requested.advance("completed")
    assert completed.cancel_requested
    with pytest.raises(DeliveryRefusal) as failure:
        completed.request_cancel()
    assert failure.value.code == "invalid_transition"


def test_terminal_and_skipped_stage_transitions_refuse() -> None:
    """Transport queueing or boundary delivery cannot be promoted to completion."""
    with pytest.raises(DeliveryRefusal) as skipped:
        DeliveryLifecycle("queued").advance("completed")
    assert skipped.value.code == "invalid_transition"
    for terminal in ("failed", "rejected", "expired", "cancelled", "superseded"):
        if terminal == "failed":
            state = DeliveryLifecycle("acknowledged").advance(terminal)
        else:
            state = DeliveryLifecycle("queued").advance(terminal)
        with pytest.raises(DeliveryRefusal):
            state.advance("queued")
