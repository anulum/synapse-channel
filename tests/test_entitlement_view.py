# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — entitlement projection tests
"""Check private and redacted read contracts across different quota units."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synapse_channel.core.entitlement_view import entitlement_view


def test_distinct_units_and_private_account_labels() -> None:
    common = {
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
    }
    events = [
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "a1",
            "label": "Confidential A",
            "status": "suspended",
        },
        {
            **common,
            "event_id": "a2",
            "kind": "account",
            "account_id": "a2",
            "label": "Confidential B",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "p1",
            "account_id": "a1",
            "unit": "tokens",
        },
        {
            **common,
            "event_id": "p2",
            "kind": "pool",
            "pool_id": "p2",
            "account_id": "a2",
            "unit": "USD",
        },
    ]
    at = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    owner = entitlement_view(events, as_of=at, private=True)
    assert [pool["unit"] for pool in owner["pools"]] == ["tokens", "USD"]
    assert owner["pools"][0]["account_usable"] is False
    assert owner["pools"][1]["account_usable"] is True
    assert "total_remaining" not in owner
    public = entitlement_view(events, as_of=at)
    assert public["private_details"] == "withheld"
    assert "accounts" not in public and "pools" not in public
    assert "Confidential" not in str(public)
    events[1]["expires_at"] = "2026-09-19T11:00:00Z"
    expired = entitlement_view(events, as_of=at, private=True)
    assert expired["accounts"][1]["status"] == "expired"
    assert expired["pools"][1]["account_usable"] is False


def test_observed_balance_and_later_usage_keep_source_evidence() -> None:
    common = {
        "recorded_at": "2026-09-19T12:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
    }
    events: list[dict[str, object]] = [
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "account-1",
            "label": "Private",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "pool-1",
            "account_id": "account-1",
            "unit": "tokens",
        },
        {
            **common,
            "event_id": "w1",
            "kind": "window",
            "window_id": "window-1",
            "pool_id": "pool-1",
            "starts_at": "2026-09-19T00:00:00Z",
            "ends_at": "2026-09-20T00:00:00Z",
            "grant": "100",
            "unit": "tokens",
            "price_revision": "price-1",
        },
        {
            **common,
            "event_id": "b1",
            "kind": "balance",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-balance-1",
            "remaining": "80",
            "observed_at": "2026-09-19T10:00:00Z",
            "source": "official:host",
            "confidence": "official",
        },
        {
            **common,
            "event_id": "u1",
            "kind": "usage",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-usage-1",
            "amount": "5",
            "observed_at": "2026-09-19T11:00:00Z",
            "source": "official:host",
            "confidence": "official",
        },
    ]
    at = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    window = entitlement_view(events, as_of=at, private=True)["pools"][0]["windows"][0]
    assert window["remaining"] == "75"
    assert window["balance_evidence"] == "observed_plus_recorded_usage"
    assert window["observation_source"] == "official:host"
    assert window["forecast"]["state"] == "insufficient_data"
    events.append(
        {
            **common,
            "event_id": "b2",
            "kind": "balance",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-balance-2",
            "remaining": None,
            "observed_at": "2026-09-19T11:30:00Z",
        }
    )
    missing = entitlement_view(events, as_of=at, private=True)["pools"][0]["windows"][0]
    assert missing["remaining"] is None
    assert missing["balance_evidence"] == "unknown"
    events.pop()
    events.pop(3)
    usage_only = entitlement_view(events, as_of=at, private=True)["pools"][0]["windows"][0]
    assert usage_only["balance_evidence"] == "incomplete_usage_estimate"
    assert usage_only["remaining"] == "95"
    after = datetime(2026, 9, 20, 1, tzinfo=timezone.utc)
    outside = entitlement_view(events, as_of=after, private=True)["pools"][0]["windows"][0]
    assert outside["remaining"] is None
    assert outside["balance_evidence"] == "outside_window"


def test_no_observation_is_unknown_and_naive_clock_is_refused() -> None:
    common = {
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
    }
    events = [
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "a",
            "label": "Private",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "p",
            "account_id": "a",
            "unit": "tokens",
        },
        {
            **common,
            "event_id": "w1",
            "kind": "window",
            "window_id": "w",
            "pool_id": "p",
            "starts_at": "2026-09-19T00:00:00Z",
            "ends_at": "2026-09-20T00:00:00Z",
            "grant": "100",
            "unit": "tokens",
            "price_revision": "r1",
        },
    ]
    report = entitlement_view(
        events, as_of=datetime(2026, 9, 19, 12, tzinfo=timezone.utc), private=True
    )
    assert report["pools"][0]["windows"][0]["balance_evidence"] == "unknown"
    with pytest.raises(ValueError, match="UTC offset"):
        entitlement_view(events, as_of=datetime(2026, 9, 19, 12), private=True)
