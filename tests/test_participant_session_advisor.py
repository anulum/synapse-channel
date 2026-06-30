# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the session advisor
"""Tests for :mod:`synapse_channel.participants.session_advisor`.

The advisor is pure over metrics and thresholds. The suite asserts each signal fires only at its
threshold, that a disabled check raises nothing, that several signals can fire at once, and that the
advice is purely descriptive (no action is implied).
"""

from __future__ import annotations

from synapse_channel.participants.session_advisor import (
    AdvisorThresholds,
    SessionSignal,
    assess_session,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


def test_no_advice_when_nothing_crosses_a_threshold() -> None:
    advice = assess_session(SessionMetrics(turns=1), AdvisorThresholds())
    assert advice.is_empty is True
    assert advice.signals == frozenset()


def test_compaction_advised_when_context_fills() -> None:
    metrics = SessionMetrics(turns=1, last_input_tokens=8500)
    thresholds = AdvisorThresholds(context_window_tokens=10000, compact_at_fraction=0.8)
    advice = assess_session(metrics, thresholds)
    assert SessionSignal.COMPACT_SOON in advice.signals
    reason = next(
        r.reason for r in advice.recommendations if r.signal is SessionSignal.COMPACT_SOON
    )
    assert "85%" in reason


def test_compaction_not_advised_below_the_fraction() -> None:
    metrics = SessionMetrics(turns=1, last_input_tokens=5000)
    thresholds = AdvisorThresholds(context_window_tokens=10000, compact_at_fraction=0.8)
    assert assess_session(metrics, thresholds).is_empty is True


def test_compaction_check_disabled_with_zero_window() -> None:
    metrics = SessionMetrics(turns=1, last_input_tokens=999999)
    assert assess_session(metrics, AdvisorThresholds(context_window_tokens=0)).is_empty is True


def test_log_advised_on_the_cadence() -> None:
    thresholds = AdvisorThresholds(log_every_turns=5)
    assert SessionSignal.LOG_NOW in assess_session(SessionMetrics(turns=5), thresholds).signals
    assert SessionSignal.LOG_NOW in assess_session(SessionMetrics(turns=10), thresholds).signals
    assert assess_session(SessionMetrics(turns=7), thresholds).is_empty is True
    # The cadence check never fires at zero turns even though 0 % n == 0.
    assert assess_session(SessionMetrics(turns=0), thresholds).is_empty is True


def test_over_budget_advised_when_spend_reaches_ceiling() -> None:
    thresholds = AdvisorThresholds(budget_usd=1.0)
    assert (
        SessionSignal.OVER_BUDGET
        in assess_session(SessionMetrics(turns=1, cost_usd=1.0), thresholds).signals
    )
    assert assess_session(SessionMetrics(turns=1, cost_usd=0.9), thresholds).is_empty is True
    # A None budget disables the check entirely.
    assert (
        assess_session(
            SessionMetrics(turns=1, cost_usd=999.0), AdvisorThresholds(budget_usd=None)
        ).is_empty
        is True
    )


def test_rate_limit_warning_at_threshold() -> None:
    thresholds = AdvisorThresholds(rate_limit_warn_at=0.85)
    hot = SessionMetrics(turns=1, max_rate_limit_utilisation=0.9)
    assert SessionSignal.APPROACHING_RATE_LIMIT in assess_session(hot, thresholds).signals
    cool = SessionMetrics(turns=1, max_rate_limit_utilisation=0.5)
    assert assess_session(cool, thresholds).is_empty is True
    none = SessionMetrics(turns=1, max_rate_limit_utilisation=None)
    assert assess_session(none, thresholds).is_empty is True


def test_high_error_rate_only_after_minimum_turns() -> None:
    thresholds = AdvisorThresholds(error_rate_warn_at=0.5, min_turns_for_error_rate=3)
    # Two of two errored is 100%, but below the minimum-turns floor it is not flagged.
    early = SessionMetrics(turns=2, errors=2)
    assert assess_session(early, thresholds).is_empty is True
    flagged = SessionMetrics(turns=4, errors=3)
    assert SessionSignal.HIGH_ERROR_RATE in assess_session(flagged, thresholds).signals


def test_several_signals_fire_together() -> None:
    metrics = SessionMetrics(
        turns=10,
        errors=6,
        cost_usd=2.0,
        last_input_tokens=9000,
        max_rate_limit_utilisation=0.95,
    )
    thresholds = AdvisorThresholds(
        context_window_tokens=10000,
        log_every_turns=5,
        budget_usd=1.5,
        rate_limit_warn_at=0.85,
        error_rate_warn_at=0.5,
        min_turns_for_error_rate=3,
    )
    advice = assess_session(metrics, thresholds)
    assert advice.signals == {
        SessionSignal.COMPACT_SOON,
        SessionSignal.LOG_NOW,
        SessionSignal.OVER_BUDGET,
        SessionSignal.APPROACHING_RATE_LIMIT,
        SessionSignal.HIGH_ERROR_RATE,
    }
    # Every recommendation carries a reason.
    assert all(rec.reason for rec in advice.recommendations)
