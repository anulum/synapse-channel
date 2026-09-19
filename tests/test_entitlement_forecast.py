# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — quota forecast behaviour tests
"""Exercise advisory forecast uncertainty against real ledger event contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synapse_channel.core.entitlement_forecast import forecast_window


def _base() -> list[dict[str, object]]:
    common = {
        "recorded_at": "2026-09-19T00:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
    }
    return [
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
            "grant": "1000",
            "unit": "tokens",
            "price_revision": "price-1",
        },
    ]


def _balance(event_id: str, hour: int, remaining: str | None) -> dict[str, object]:
    return {
        "event_id": event_id,
        "kind": "balance",
        "recorded_at": f"2026-09-19T{hour:02d}:05:00Z",
        "source": "official:host",
        "confidence": "official",
        "window_id": "window-1",
        "window_event_id": "w1",
        "source_event_id": f"host-{event_id}",
        "remaining": remaining,
        "observed_at": f"2026-09-19T{hour:02d}:00:00Z",
    }


def _at(hour: int, minute: int = 10) -> datetime:
    return datetime(2026, 9, 19, hour, minute, tzinfo=timezone.utc)


def test_burst_usage_lowers_forecast_confidence() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "1000"),
        _balance("b2", 2, "990"),
        _balance("b3", 3, "500"),
    ]
    forecast = forecast_window(events, "window-1", as_of=_at(3))
    assert forecast.state == "depleting"
    assert forecast.sample_size == 3
    assert forecast.confidence == "low"
    assert forecast.remaining == "500"
    assert forecast.rate_per_second is not None


def test_quiet_and_missing_balance_are_visible() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "900"),
        _balance("b2", 2, "900"),
        _balance("b3", 3, "900"),
    ]
    quiet = forecast_window(events, "window-1", as_of=_at(3))
    assert quiet.state == "quiet"
    assert quiet.estimated_exhaustion_at is None
    events.append(_balance("b4", 4, None))
    missing = forecast_window(events, "window-1", as_of=_at(4))
    assert missing.state == "insufficient_data"
    assert missing.remaining is None


def test_price_revision_invalidates_old_samples() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "900"),
        _balance("b2", 2, "800"),
        _balance("b3", 3, "700"),
    ]
    assert forecast_window(events, "window-1", as_of=_at(3)).state == "depleting"
    events.append(
        {
            **events[2],
            "event_id": "w2",
            "recorded_at": "2026-09-19T04:00:00Z",
            "price_revision": "price-2",
            "supersedes": "w1",
        }
    )
    changed = forecast_window(events, "window-1", as_of=_at(5))
    assert changed.state == "insufficient_data"
    assert changed.sample_size == 0


def test_outside_window_and_stale_sample_have_no_forecast() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "900"),
        _balance("b2", 2, "800"),
        _balance("b3", 3, "700"),
    ]
    assert forecast_window(events, "window-1", as_of=_at(0)).state == "insufficient_data"
    assert (
        forecast_window(events, "window-1", as_of=_at(12)).reason
        == "latest balance observation is stale"
    )


def test_insufficient_short_span_and_unreconciled_usage() -> None:
    events = [*_base(), _balance("b1", 1, "900")]
    assert forecast_window(events, "window-1", as_of=_at(1)).sample_size == 1
    events.extend(
        [
            {**_balance("b2", 1, "800"), "observed_at": "2026-09-19T01:10:00Z"},
            {**_balance("b3", 1, "700"), "observed_at": "2026-09-19T01:20:00Z"},
        ]
    )
    assert "under one hour" in forecast_window(events, "window-1", as_of=_at(1, 30)).reason
    events.append(
        {
            "event_id": "u1",
            "kind": "usage",
            "recorded_at": "2026-09-19T02:05:00Z",
            "source": "official:host",
            "confidence": "official",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-u1",
            "amount": "2",
            "observed_at": "2026-09-19T02:00:00Z",
        }
    )
    assert "lacks a reconciled balance" in forecast_window(events, "window-1", as_of=_at(2)).reason


def test_unknown_window_and_naive_clock_are_refused() -> None:
    assert forecast_window(_base(), "window-1", as_of=_at(1)).sample_size == 0
    with pytest.raises(ValueError, match="unknown quota window"):
        forecast_window(_base(), "missing", as_of=_at(1))
    with pytest.raises(ValueError, match="UTC offset"):
        forecast_window(_base(), "window-1", as_of=datetime(2026, 9, 19, 1))


def test_rising_and_zero_balance_do_not_infer_spend_authority() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "700"),
        _balance("b2", 2, "800"),
        _balance("b3", 3, "600"),
    ]
    assert "rose" in forecast_window(events, "window-1", as_of=_at(3)).reason
    events[4]["remaining"] = "500"
    events[5]["remaining"] = "0"
    depleted = forecast_window(events, "window-1", as_of=_at(3))
    assert depleted.state == "depleted"
    assert depleted.estimated_exhaustion_at is None


def test_fast_depletion_estimate_stays_advisory() -> None:
    events = [
        *_base(),
        _balance("b1", 1, "900"),
        _balance("b2", 2, "500"),
        _balance("b3", 3, "100"),
    ]
    result = forecast_window(events, "window-1", as_of=_at(3))
    assert result.state == "depleting"
    assert result.estimated_exhaustion_at is not None
    assert "rate uses" in result.reason
    slow = [*_base(), _balance("b1", 1, "900"), _balance("b2", 2, "899"), _balance("b3", 3, "898")]
    outside = forecast_window(slow, "window-1", as_of=_at(3))
    assert outside.estimated_exhaustion_at is None
    assert "after window end" in outside.reason


def test_out_of_order_arrival_and_new_window_reset() -> None:
    events = [
        *_base(),
        _balance("b3", 3, "700"),
        _balance("b1", 1, "900"),
        _balance("b2", 2, "800"),
    ]
    first = forecast_window(events, "window-1", as_of=_at(3))
    assert first.state == "depleting"
    assert first.sample_size == 3
    events.append(
        {
            **events[2],
            "event_id": "w2",
            "window_id": "window-2",
            "recorded_at": "2026-09-20T00:00:00Z",
            "starts_at": "2026-09-20T00:00:00Z",
            "ends_at": "2026-09-21T00:00:00Z",
        }
    )
    new_period = forecast_window(
        events, "window-2", as_of=datetime(2026, 9, 20, 1, tzinfo=timezone.utc)
    )
    assert new_period.state == "insufficient_data"
    assert new_period.sample_size == 0
