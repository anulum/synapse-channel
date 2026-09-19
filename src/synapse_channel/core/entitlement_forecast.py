# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — advisory quota-window depletion forecasts
"""Estimate quota depletion only from current-window balance observations.

Forecasts are descriptive evidence. They never reserve a pool, permit a model
call, or enforce spending. A reset or window/price correction starts a new
revision and invalidates samples attached to the earlier revision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from synapse_channel.core.entitlements import active_events, parse_quantity, parse_time


@dataclass(frozen=True, slots=True)
class WindowForecast:
    """Advisory depletion estimate for one current quota-window revision.

    Attributes
    ----------
    state : str
        ``insufficient_data``, ``quiet``, ``depleting`` or ``depleted``.
    sample_size : int
        Number of usable balance samples in this revision.
    observation_age_seconds : float or None
        Age of the newest sample at projection time.
    confidence : str
        ``none``, ``low`` or ``moderate``; never an authorisation signal.
    remaining : str or None
        Last observed balance as a decimal string, when known.
    rate_per_second : str or None
        Non-negative observed depletion rate in window units per second.
    estimated_exhaustion_at : str or None
        UTC ISO timestamp if an estimate is supported by the samples.
    reason : str
        Explicit explanation for the forecast state.
    """

    state: str
    sample_size: int
    observation_age_seconds: float | None
    confidence: str
    remaining: str | None
    rate_per_second: str | None
    estimated_exhaustion_at: str | None
    reason: str


def _insufficient(
    size: int, age: float | None, remaining: str | None, reason: str
) -> WindowForecast:
    """Return an explicit insufficient-data result without numeric authority."""
    return WindowForecast("insufficient_data", size, age, "none", remaining, None, None, reason)


def forecast_window(
    events: Sequence[Mapping[str, object]],
    window_id: str,
    *,
    as_of: datetime,
) -> WindowForecast:
    """Estimate depletion from at least three comparable balance samples.

    Samples must belong to the active window revision, be ordered by their
    observation timestamp and cover at least one hour. A rising balance is
    treated as unmodelled replenishment rather than negative consumption.

    Parameters
    ----------
    events : Sequence[Mapping[str, object]]
        Complete validated ledger event stream in insertion order.
    window_id : str
        Quota window whose current revision is forecast.
    as_of : datetime.datetime
        Offset-aware evaluation time.

    Returns
    -------
    WindowForecast
        Advisory estimate or a visible insufficient-data state.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a UTC offset")
    active = active_events(events)
    window = next(
        (
            entry
            for entry in active
            if entry["kind"] == "window" and entry["window_id"] == window_id
        ),
        None,
    )
    if window is None:
        raise ValueError("unknown quota window")
    start = parse_time(window["starts_at"], "starts_at")
    end = parse_time(window["ends_at"], "ends_at")
    if not start <= as_of < end:
        return _insufficient(0, None, None, "outside current quota window")
    current_observations = [
        entry
        for entry in active
        if entry["kind"] in {"balance", "usage"}
        and entry["window_id"] == window_id
        and entry["window_event_id"] == window["event_id"]
        and parse_time(entry["observed_at"], "observed_at") <= as_of
    ]
    if current_observations:
        latest = max(
            current_observations,
            key=lambda entry: parse_time(entry["observed_at"], "observed_at"),
        )
        if latest["kind"] == "usage" or latest.get("remaining") is None:
            return _insufficient(0, None, None, "latest usage lacks a reconciled balance")
    samples = sorted(
        (
            (
                parse_time(entry["observed_at"], "observed_at"),
                parse_quantity(entry["remaining"], "remaining"),
            )
            for entry in current_observations
            if entry["kind"] == "balance" and entry["remaining"] is not None
        ),
        key=lambda sample: sample[0],
    )
    if not samples:
        return _insufficient(0, None, None, "no current-revision balance observations")
    newest_time, newest_balance = samples[-1]
    age = (as_of - newest_time).total_seconds()
    remaining = str(newest_balance)
    if len(samples) < 3:
        return _insufficient(
            len(samples), age, remaining, "at least three balance samples required"
        )
    span = (newest_time - samples[0][0]).total_seconds()
    if span < 3600:
        return _insufficient(len(samples), age, remaining, "observation span is under one hour")
    if age > min(86400, (end - start).total_seconds() / 4):
        return _insufficient(len(samples), age, remaining, "latest balance observation is stale")
    rates: list[Decimal] = []
    for (earlier_time, earlier_balance), (later_time, later_balance) in zip(
        samples, samples[1:], strict=False
    ):
        if later_balance > earlier_balance:
            return _insufficient(len(samples), age, remaining, "balance rose within one revision")
        seconds = Decimal(str((later_time - earlier_time).total_seconds()))
        rates.append((earlier_balance - later_balance) / seconds)
    if newest_balance == 0:
        return WindowForecast(
            "depleted",
            len(samples),
            age,
            "moderate",
            remaining,
            "0",
            None,
            "last reported balance is zero",
        )
    if not any(rates):
        return WindowForecast(
            "quiet", len(samples), age, "low", remaining, "0", None, "no depletion observed"
        )
    total_rate = (samples[0][1] - newest_balance) / Decimal(str(span))
    rate = max(total_rate, rates[-1])
    variation = max(rates) / min((item for item in rates if item > 0), default=rate)
    confidence = "moderate" if len(samples) >= 5 and variation <= 2 else "low"
    seconds_to_zero = newest_balance / rate
    if seconds_to_zero > Decimal(str((end - as_of).total_seconds())):
        exhaustion = None
        reason = "projected depletion falls after window end"
    else:
        exhaustion = (as_of + timedelta(seconds=float(seconds_to_zero))).isoformat()
        reason = "rate uses the greater of whole-span and latest-interval depletion"
    return WindowForecast(
        "depleting", len(samples), age, confidence, remaining, str(rate), exhaustion, reason
    )
