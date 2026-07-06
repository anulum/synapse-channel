# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the honesty-bound cross-hub dead-letter forwarding notice

from __future__ import annotations

from synapse_channel.core.dead_letter_forwarding import (
    DeadLetterForwardError,
    forwarding_notice,
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
