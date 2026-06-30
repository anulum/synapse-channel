# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for running session telemetry
"""Tests for :mod:`synapse_channel.participants.session_telemetry`.

The fold is pure, so the suite drives it with built turn results and asserts the running totals,
the current-context (last input) signal, the highest-utilisation tracking, the latency clamp, and
the derived rates.
"""

from __future__ import annotations

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.session_telemetry import SessionMetrics, accumulate
from synapse_channel.participants.stream_json import StreamOutcome


def _turn(
    *,
    answer: str = "ok",
    cost: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    utilisation: float | None = None,
) -> TurnResult:
    return build_turn_result(
        participant="SC/p",
        channel=ParticipantChannel.HEADLESS,
        request=TurnRequest(topic_id="t", prompt="p"),
        outcome=StreamOutcome(
            answer=answer,
            rationale="",
            session_id="",
            is_error=False,
            subtype="success",
            cost_usd=cost,
            num_turns=1,
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            rate_limit_utilisation=utilisation,
        ),
    )


def _error_turn() -> TurnResult:
    return error_turn_result(
        participant="SC/p",
        channel=ParticipantChannel.HEADLESS,
        request=TurnRequest(topic_id="t", prompt="p"),
        reason="boom",
    )


def test_initial_metrics_are_zero() -> None:
    metrics = SessionMetrics()
    assert metrics.turns == 0
    assert metrics.total_tokens == 0
    assert metrics.mean_latency_seconds == 0.0
    assert metrics.error_rate == 0.0
    assert metrics.max_rate_limit_utilisation is None


def test_folds_one_turn() -> None:
    metrics = accumulate(
        SessionMetrics(),
        _turn(cost=0.25, input_tokens=120, output_tokens=30, utilisation=0.4),
        latency_seconds=2.0,
    )
    assert metrics.turns == 1
    assert metrics.errors == 0
    assert metrics.input_tokens == 120
    assert metrics.output_tokens == 30
    assert metrics.total_tokens == 150
    assert metrics.cost_usd == 0.25
    assert metrics.total_latency_seconds == 2.0
    assert metrics.mean_latency_seconds == 2.0
    assert metrics.last_input_tokens == 120
    assert metrics.max_rate_limit_utilisation == 0.4


def test_accumulates_across_turns_and_tracks_current_context() -> None:
    metrics = SessionMetrics()
    metrics = accumulate(metrics, _turn(input_tokens=100, cost=0.1), latency_seconds=1.0)
    metrics = accumulate(metrics, _turn(input_tokens=250, cost=0.2), latency_seconds=3.0)
    assert metrics.turns == 2
    assert metrics.input_tokens == 350
    assert abs(metrics.cost_usd - 0.3) < 1e-9
    assert metrics.mean_latency_seconds == 2.0
    # The current context is the latest turn's input, not the cumulative sum.
    assert metrics.last_input_tokens == 250


def test_counts_errors_and_abstentions() -> None:
    metrics = SessionMetrics()
    metrics = accumulate(metrics, _turn(answer="ok"), latency_seconds=1.0)
    metrics = accumulate(metrics, _error_turn(), latency_seconds=1.0)
    metrics = accumulate(metrics, _turn(answer="   "), latency_seconds=1.0)  # abstains
    assert metrics.turns == 3
    assert metrics.errors == 1
    assert metrics.abstentions == 1
    assert abs(metrics.error_rate - 1 / 3) < 1e-9


def test_tracks_highest_utilisation_ignoring_missing() -> None:
    metrics = SessionMetrics()
    metrics = accumulate(metrics, _turn(utilisation=0.3), latency_seconds=0.0)
    metrics = accumulate(metrics, _turn(utilisation=None), latency_seconds=0.0)
    metrics = accumulate(metrics, _turn(utilisation=0.7), latency_seconds=0.0)
    metrics = accumulate(metrics, _turn(utilisation=0.5), latency_seconds=0.0)
    assert metrics.max_rate_limit_utilisation == 0.7


def test_first_utilisation_sets_the_max_from_none() -> None:
    metrics = accumulate(SessionMetrics(), _turn(utilisation=0.2), latency_seconds=0.0)
    assert metrics.max_rate_limit_utilisation == 0.2


def test_negative_latency_is_clamped_to_zero() -> None:
    metrics = accumulate(SessionMetrics(), _turn(), latency_seconds=-5.0)
    assert metrics.total_latency_seconds == 0.0
