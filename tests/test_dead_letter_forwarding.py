# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the honesty-bound cross-hub dead-letter forwarding notice

from __future__ import annotations

import pytest

from synapse_channel.core.dead_letter_forwarding import (
    FORWARDING_FIELD,
    DeadLetterForwardError,
    DeadLetterForwardingWireError,
    ForwardingNotice,
    decode_forwarding_notice,
    forwarding_notice,
    incoming_forwarding_notice,
)


def test_forwarding_notice_names_the_target_count_and_both_hubs() -> None:
    notice = forwarding_notice(
        "OWNED/reader", 3, origin_hub_id="syn-edge", owner_hub_id="syn-owner"
    )
    assert notice == {
        "target": "OWNED/reader",
        "count": 3,
        "origin_hub_id": "syn-edge",
        "owner_hub_id": "syn-owner",
    }


def test_forwarding_notice_carries_no_message_body() -> None:
    # The honesty bound: the ledger holds counts and names, never bodies, so the notice cannot
    # carry one across the trust boundary. Guard the shape against a future field leaking a payload.
    notice = forwarding_notice("OWNED/reader", 1, origin_hub_id="a", owner_hub_id="b")
    assert set(notice) == {"target", "count", "origin_hub_id", "owner_hub_id"}
    for leaky in ("payload", "body", "message", "text"):
        assert leaky not in notice


def test_forward_error_is_a_runtime_error() -> None:
    # A single failure type callers catch to treat forwarding as best-effort over the audit.
    assert issubclass(DeadLetterForwardError, RuntimeError)


def _frame(notice: dict[str, object]) -> dict[str, object]:
    """Nest a notice under the wire field the transport uses, as an inbound frame carries it."""
    return {"type": "dead_letter_forwarding", "sender": "syn-edge", FORWARDING_FIELD: notice}


def test_decode_round_trips_a_forwarded_notice() -> None:
    # The pointer a hub builds and nests on the wire decodes back to the same four fields.
    notice = forwarding_notice(
        "OWNED/reader", 3, origin_hub_id="syn-edge", owner_hub_id="syn-owner"
    )
    decoded = decode_forwarding_notice(_frame(notice))
    assert decoded == ForwardingNotice(
        target="OWNED/reader", count=3, origin_hub_id="syn-edge", owner_hub_id="syn-owner"
    )


def test_decode_accepts_a_zero_count() -> None:
    # A zero undelivered count is a valid (if unusual) pointer, not a malformed one.
    decoded = decode_forwarding_notice(_frame({**_valid(), "count": 0}))
    assert decoded.count == 0


def _valid() -> dict[str, object]:
    """A minimal valid pointer body."""
    return {"target": "OWNED/reader", "count": 1, "origin_hub_id": "a", "owner_hub_id": "b"}


def test_decode_rejects_a_frame_with_no_pointer() -> None:
    with pytest.raises(DeadLetterForwardingWireError):
        decode_forwarding_notice({"type": "dead_letter_forwarding", "sender": "syn-edge"})


def test_decode_rejects_a_non_mapping_pointer() -> None:
    with pytest.raises(DeadLetterForwardingWireError):
        decode_forwarding_notice({FORWARDING_FIELD: ["not", "a", "mapping"]})


@pytest.mark.parametrize("target", ["", "   ", None, 7])
def test_decode_rejects_a_missing_or_blank_target(target: object) -> None:
    with pytest.raises(DeadLetterForwardingWireError):
        decode_forwarding_notice(_frame({**_valid(), "target": target}))


@pytest.mark.parametrize("count", [-1, True, 1.5, "3", None])
def test_decode_rejects_a_non_natural_count(count: object) -> None:
    # A count is a cardinality: negative, boolean, float, string, and absent are all refused.
    with pytest.raises(DeadLetterForwardingWireError):
        decode_forwarding_notice(_frame({**_valid(), "count": count}))


@pytest.mark.parametrize("field", ["origin_hub_id", "owner_hub_id"])
def test_decode_rejects_a_non_string_hub_id(field: str) -> None:
    with pytest.raises(DeadLetterForwardingWireError):
        decode_forwarding_notice(_frame({**_valid(), field: 42}))


def test_wire_error_is_a_value_error() -> None:
    assert issubclass(DeadLetterForwardingWireError, ValueError)


def test_incoming_notice_names_the_peer_target_and_count_but_no_body() -> None:
    text = incoming_forwarding_notice("OWNED/reader", 4, "syn-edge")
    assert "OWNED/reader" in text
    assert "4" in text
    assert "syn-edge" in text
    # The operator line is still a pointer: it never reconstructs a message body.
    for leaky in ("payload", "body", "secret"):
        assert leaky not in text
