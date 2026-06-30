# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for opt-in automatic reactions to advisory signals
"""Tests for :mod:`synapse_channel.participants.auto_action`.

A recording handler stands in for the operator's real compact/log/handover side effects, so the
suite asserts the reactor fires an action only when its signal is present, the action is armed, and
a handler is supplied — opt-in on every axis — ignores signals with no mapped action, preserves the
advisor's recommendation order, and hands each fired handler the round's context.
"""

from __future__ import annotations

from synapse_channel.participants.auto_action import (
    AutoAction,
    AutoActionContext,
    AutoActionDispatch,
    AutoActionPolicy,
    react_to_advice,
)
from synapse_channel.participants.session_advisor import (
    Recommendation,
    SessionAdvice,
    SessionSignal,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


class _Recorder:
    """Capture every context a handler is fired with."""

    def __init__(self) -> None:
        self.calls: list[AutoActionContext] = []

    async def __call__(self, context: AutoActionContext) -> None:
        self.calls.append(context)


def _advice(*signals: SessionSignal) -> SessionAdvice:
    return SessionAdvice(
        recommendations=tuple(Recommendation(s, f"reason-{s.value}") for s in signals)
    )


async def test_fires_armed_action_with_handler_and_passes_context() -> None:
    recorder = _Recorder()
    metrics = SessionMetrics(turns=4)
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy(armed=frozenset({AutoAction.COMPACT})),
        handlers={AutoAction.COMPACT: recorder},
    )
    advice = _advice(SessionSignal.COMPACT_SOON)

    fired = await react_to_advice(
        advice, dispatch, session_id="topic-x", round_index=2, metrics=metrics
    )

    assert fired == (AutoAction.COMPACT,)
    assert len(recorder.calls) == 1
    context = recorder.calls[0]
    assert context.session_id == "topic-x"
    assert context.round_index == 2
    assert context.action is AutoAction.COMPACT
    assert context.recommendation.signal is SessionSignal.COMPACT_SOON
    assert context.metrics is metrics
    assert context.advice is advice


async def test_does_not_fire_when_action_not_armed() -> None:
    recorder = _Recorder()
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy(),  # arms nothing
        handlers={AutoAction.LOG: recorder},
    )

    fired = await react_to_advice(
        _advice(SessionSignal.LOG_NOW),
        dispatch,
        session_id="t",
        round_index=0,
        metrics=SessionMetrics(),
    )

    assert fired == ()
    assert recorder.calls == []


async def test_does_not_fire_when_no_handler_supplied() -> None:
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy(armed=frozenset({AutoAction.COMPACT})),
        handlers={},  # armed but unhandled
    )

    fired = await react_to_advice(
        _advice(SessionSignal.COMPACT_SOON),
        dispatch,
        session_id="t",
        round_index=0,
        metrics=SessionMetrics(),
    )

    assert fired == ()


async def test_ignores_signals_with_no_mapped_action() -> None:
    recorder = _Recorder()
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy.all_on(),
        handlers={action: recorder for action in AutoAction},
    )

    # over-budget halts the loop and approaching-rate-limit is handled by the router; neither maps
    # to an automatic action here, so an armed, fully-handled dispatch still fires nothing.
    fired = await react_to_advice(
        _advice(SessionSignal.OVER_BUDGET, SessionSignal.APPROACHING_RATE_LIMIT),
        dispatch,
        session_id="t",
        round_index=0,
        metrics=SessionMetrics(),
    )

    assert fired == ()
    assert recorder.calls == []


async def test_fires_multiple_actions_in_advisory_order() -> None:
    recorder = _Recorder()
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy.all_on(),
        handlers={action: recorder for action in AutoAction},
    )

    fired = await react_to_advice(
        _advice(
            SessionSignal.COMPACT_SOON,
            SessionSignal.LOG_NOW,
            SessionSignal.HIGH_ERROR_RATE,
        ),
        dispatch,
        session_id="t",
        round_index=1,
        metrics=SessionMetrics(),
    )

    assert fired == (AutoAction.COMPACT, AutoAction.LOG, AutoAction.HANDOVER)
    assert [c.action for c in recorder.calls] == list(fired)


async def test_empty_advice_fires_nothing() -> None:
    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy.all_on(),
        handlers={action: _Recorder() for action in AutoAction},
    )

    fired = await react_to_advice(
        _advice(), dispatch, session_id="t", round_index=0, metrics=SessionMetrics()
    )

    assert fired == ()


def test_policy_default_arms_nothing_and_all_on_arms_everything() -> None:
    assert AutoActionPolicy().armed == frozenset()
    assert AutoActionPolicy.all_on().armed == frozenset(AutoAction)
