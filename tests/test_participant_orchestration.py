# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routed, telemetered deliberation loop
"""Tests for :mod:`synapse_channel.participants.orchestration`.

Fake participants return scripted turns over a fake clock, so the suite asserts the loop routes
each round, folds telemetry, threads the previous turn as framed context, steers away from a
rate-limited provider, persists a durable snapshot when a poster is supplied, and stops correctly
on an unroutable round, an over-budget signal, an empty roster, or a non-positive round count.
"""

from __future__ import annotations

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import ProviderCapabilities
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.exchange import REACTION_DIRECTIVE
from synapse_channel.participants.orchestration import (
    STOPPED_BUDGET,
    STOPPED_COMPLETED,
    STOPPED_EMPTY,
    STOPPED_UNROUTABLE,
    OrchestrationSeat,
    orchestrate_session,
)
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.provider_route import ModelCandidate, TaskProfile
from synapse_channel.participants.session_advisor import AdvisorThresholds, SessionSignal


class _FakeParticipant:
    """A participant that replays scripted turns and records the requests it received."""

    def __init__(self, identity: str, results: list[TurnResult]) -> None:
        self._identity = identity
        self._results = results
        self.requests: list[TurnRequest] = []

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.MCP

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.requests.append(request)
        return self._results.pop(0)

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(
            identity=self._identity, channel=ParticipantChannel.MCP, available=True, detail="ok"
        )


class _Clock:
    """A monotonic clock advancing one second per read, so each turn measures a 1s latency."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 1.0
        return self.t


class _RecordingPoster:
    """Capture every progress note posted, mimicking ``SynapseAgent.post_progress``."""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    async def __call__(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.notes.append((task_id, text, kind))


def _result(
    *,
    cost_usd: float = 0.0,
    is_error: bool = False,
    rate_limit_utilisation: float | None = None,
    answer: str = "ok",
    model: str = "m",
) -> TurnResult:
    return TurnResult(
        kind="turn_result",
        participant="p",
        channel="mcp",
        topic_id="t",
        answer=answer,
        rationale="",
        abstained=False,
        is_error=is_error,
        reason="",
        session="",
        cost_usd=cost_usd,
        stop_reason="end_turn",
        model=model,
        input_tokens=10,
        output_tokens=5,
        rate_limit_utilisation=rate_limit_utilisation,
    )


def _seat(
    name: str,
    results: list[TurnResult],
    *,
    model: str = "m",
    util: float | None = None,
    price: ModelPrice | None = None,
) -> OrchestrationSeat:
    return OrchestrationSeat(
        participant=_FakeParticipant(name, results),
        candidate=ModelCandidate(
            name=name,
            model=model,
            capabilities=ProviderCapabilities(mcp_reachable=True),
            price=price,
            rate_limit_utilisation=util,
        ),
    )


async def test_empty_roster_runs_nothing() -> None:
    transcript = await orchestrate_session(
        "q", [], rounds=3, topic_id="t", task=TaskProfile(), thresholds=AdvisorThresholds()
    )
    assert transcript.stopped == STOPPED_EMPTY
    assert transcript.rounds == ()
    assert transcript.metrics.turns == 0


async def test_non_positive_rounds_runs_nothing() -> None:
    transcript = await orchestrate_session(
        "q",
        [_seat("a", [_result()])],
        rounds=0,
        topic_id="t",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
    )
    assert transcript.stopped == STOPPED_EMPTY


async def test_completed_run_threads_context_and_accumulates() -> None:
    seat = _seat("a", [_result(answer="first"), _result(answer="second")])
    transcript = await orchestrate_session(
        "the question",
        [seat],
        rounds=2,
        topic_id="topic-1",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        shared_context="house rules",
    )
    assert transcript.stopped == STOPPED_COMPLETED
    assert len(transcript.rounds) == 2
    assert transcript.metrics.turns == 2
    assert transcript.metrics.input_tokens == 20
    # No signals fire under default thresholds for clean turns.
    assert transcript.rounds[0].advice.is_empty
    requests = seat.participant.requests  # type: ignore[attr-defined]
    # Round 0 sees only the shared context; round 1 reacts to the framed previous turn.
    assert requests[0].context == "house rules"
    assert "house rules" in requests[1].context
    assert REACTION_DIRECTIVE in requests[1].context
    assert "first" in requests[1].context


async def test_routes_away_from_a_rate_limited_provider_and_persists() -> None:
    poster = _RecordingPoster()
    posted: list[TurnResult] = []

    async def _post(result: TurnResult) -> None:
        posted.append(result)

    # 'a' wins the first round (tie on zero headroom, roster order) and reports high utilisation;
    # the second round must steer to 'b', which still has headroom.
    seat_a = _seat("a", [_result(rate_limit_utilisation=0.9)])
    seat_b = _seat("b", [_result(rate_limit_utilisation=0.2)])
    transcript = await orchestrate_session(
        "q",
        [seat_a, seat_b],
        rounds=2,
        topic_id="topic-2",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        post=_post,
        clock=_Clock(),
        which=lambda name: f"/usr/bin/{name}",
        post_progress=poster,
    )
    assert transcript.stopped == STOPPED_COMPLETED
    assert transcript.rounds[0].choice.candidate.name == "a"
    assert transcript.rounds[1].choice.candidate.name == "b"
    assert transcript.rounds[0].latency_seconds == 1.0
    # 'a' tripped the rate-limit advisory; it is recorded, not acted on.
    assert SessionSignal.APPROACHING_RATE_LIMIT in transcript.rounds[0].advice.signals
    # Each result was published, and each round persisted a durable session_metric snapshot.
    assert len(posted) == 2
    assert len(poster.notes) == 2
    assert all(
        task_id == "topic-2" and kind == "session_metric" for task_id, _, kind in poster.notes
    )


async def test_unroutable_round_stops() -> None:
    # The only seat is already at its rate limit, so nothing can be routed.
    seat = _seat("a", [_result()], util=1.0)
    transcript = await orchestrate_session(
        "q",
        [seat],
        rounds=3,
        topic_id="t",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
    )
    assert transcript.stopped == STOPPED_UNROUTABLE
    assert transcript.rounds == ()


async def test_over_budget_signal_halts_the_run() -> None:
    seat = _seat("a", [_result(cost_usd=0.6), _result(cost_usd=0.6)])
    transcript = await orchestrate_session(
        "q",
        [seat],
        rounds=3,
        topic_id="t",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(budget_usd=0.5),
    )
    assert transcript.stopped == STOPPED_BUDGET
    assert len(transcript.rounds) == 1
    assert SessionSignal.OVER_BUDGET in transcript.rounds[0].advice.signals


async def test_empty_shared_context_threads_only_the_reaction_and_peer() -> None:
    seat = _seat("a", [_result(answer="alpha"), _result(answer="beta")])
    transcript = await orchestrate_session(
        "q",
        [seat],
        rounds=2,
        topic_id="t",
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
    )
    assert transcript.stopped == STOPPED_COMPLETED
    requests = seat.participant.requests  # type: ignore[attr-defined]
    assert requests[0].context == ""
    # With no shared context, round 1 carries the reaction directive and the framed peer turn only.
    assert requests[1].context.startswith(REACTION_DIRECTIVE)
    assert "alpha" in requests[1].context
