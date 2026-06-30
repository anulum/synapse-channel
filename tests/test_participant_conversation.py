# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the multi-round conversation
"""Tests for :mod:`synapse_channel.participants.conversation`."""

from __future__ import annotations

from synapse_channel.participants.conversation import (
    STOPPED_BUDGET,
    STOPPED_COMPLETED,
    STOPPED_EMPTY,
    ResultSink,
    conduct_conversation,
)
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.exchange import REACTION_DIRECTIVE
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.peer_boundary import PEER_FENCE


class _CostingParticipant:
    """Returns a fixed answer at a fixed cost and records the context it saw."""

    def __init__(self, identity: str, answer: str, *, cost: float = 0.0) -> None:
        self._identity = identity
        self._answer = answer
        self._cost = cost
        self.contexts: list[str] = []

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.contexts.append(request.context)
        return TurnResult(
            kind="participant.turn_result",
            participant=self._identity,
            channel="headless",
            topic_id=request.topic_id,
            answer=self._answer,
            rationale="",
            abstained=False,
            is_error=False,
            reason="",
            session="",
            cost_usd=self._cost,
            stop_reason="end_turn",
            model="",
            input_tokens=0,
            output_tokens=0,
        )

    def health(self) -> ParticipantHealth:  # pragma: no cover - unused here
        return ParticipantHealth(self._identity, ParticipantChannel.HEADLESS, True, "costing")


def _recording_sink() -> tuple[list[TurnResult], ResultSink]:
    posted: list[TurnResult] = []

    async def post(result: TurnResult) -> None:
        posted.append(result)

    return posted, post


async def test_no_participants_runs_nothing() -> None:
    _posted, post = _recording_sink()
    transcript = await conduct_conversation("q", [], rounds=3, topic_id="t", post=post)
    assert transcript.stopped == STOPPED_EMPTY
    assert transcript.turns == ()
    assert transcript.total_cost_usd == 0.0


async def test_non_positive_rounds_runs_nothing() -> None:
    _posted, post = _recording_sink()
    seat = _CostingParticipant("SC/a", "x")
    transcript = await conduct_conversation("q", [seat], rounds=0, topic_id="t", post=post)
    assert transcript.stopped == STOPPED_EMPTY
    assert seat.contexts == []


async def test_rounds_cycle_through_participants_in_order() -> None:
    a = _CostingParticipant("SC/a", "from-a")
    b = _CostingParticipant("SC/b", "from-b")
    posted, post = _recording_sink()

    transcript = await conduct_conversation("q", [a, b], rounds=3, topic_id="t", post=post)

    assert transcript.stopped == STOPPED_COMPLETED
    assert [r["participant"] for r in transcript.turns] == ["SC/a", "SC/b", "SC/a"]
    assert [r["participant"] for r in posted] == ["SC/a", "SC/b", "SC/a"]


async def test_first_round_has_no_peer_block_later_rounds_react() -> None:
    a = _CostingParticipant("SC/a", "answer-a")
    b = _CostingParticipant("SC/b", "answer-b")
    _posted, post = _recording_sink()

    await conduct_conversation(
        "q", [a, b], rounds=2, topic_id="t", post=post, shared_context="rules"
    )

    # Round 0 (a): shared context only, no peer block.
    assert a.contexts[0] == "rules"
    assert PEER_FENCE not in a.contexts[0]
    # Round 1 (b): shared context + directive + fenced previous answer.
    assert "rules" in b.contexts[0]
    assert REACTION_DIRECTIVE in b.contexts[0]
    assert PEER_FENCE in b.contexts[0]
    assert "answer-a" in b.contexts[0]


async def test_budget_stops_the_conversation_early() -> None:
    a = _CostingParticipant("SC/a", "x", cost=0.5)
    b = _CostingParticipant("SC/b", "y", cost=0.5)
    posted, post = _recording_sink()

    transcript = await conduct_conversation(
        "q", [a, b], rounds=10, topic_id="t", post=post, budget_usd=0.6
    )

    # 0.5 < 0.6 after round 0 (continue); 1.0 >= 0.6 after round 1 (stop).
    assert transcript.stopped == STOPPED_BUDGET
    assert len(transcript.turns) == 2
    assert transcript.total_cost_usd == 1.0
    assert len(posted) == 2


async def test_total_cost_is_summed_over_all_turns() -> None:
    a = _CostingParticipant("SC/a", "x", cost=0.1)
    _posted, post = _recording_sink()
    transcript = await conduct_conversation("q", [a], rounds=3, topic_id="t", post=post)
    assert transcript.stopped == STOPPED_COMPLETED
    assert abs(transcript.total_cost_usd - 0.3) < 1e-9


async def test_budget_not_reached_completes_all_rounds() -> None:
    a = _CostingParticipant("SC/a", "x", cost=0.1)
    _posted, post = _recording_sink()
    transcript = await conduct_conversation(
        "q", [a], rounds=2, topic_id="t", post=post, budget_usd=100.0
    )
    assert transcript.stopped == STOPPED_COMPLETED
    assert len(transcript.turns) == 2
