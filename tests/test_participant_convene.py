# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for convening a multi-party conversation
"""Tests for :mod:`synapse_channel.participants.convene`."""

from __future__ import annotations

import pytest

from synapse_channel.participants.convene import (
    CRITIQUE_DIRECTIVE,
    SYNTHESIS_DIRECTIVE,
    ResultSink,
    convene,
)
from synapse_channel.participants.conversation import STOPPED_BUDGET, STOPPED_COMPLETED
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.modes import ConversationMode
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth
from synapse_channel.participants.peer_boundary import PEER_FENCE


class _PanelParticipant:
    """Returns a fixed answer at a fixed cost and records every context it saw."""

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
            answer=f"{self._answer}-{len(self.contexts)}",
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
        return ParticipantHealth(self._identity, ParticipantChannel.HEADLESS, True, "panel")


def _recording_sink() -> tuple[list[TurnResult], ResultSink]:
    posted: list[TurnResult] = []

    async def post(result: TurnResult) -> None:
        posted.append(result)

    return posted, post


async def test_empty_panel_is_rejected() -> None:
    _posted, post = _recording_sink()
    with pytest.raises(ValueError, match="at least one participant"):
        await convene("q", [], mode=ConversationMode.COLLOQUY, topic_id="t", post=post)


async def test_symposium_without_moderator_is_rejected() -> None:
    _posted, post = _recording_sink()
    panel = [_PanelParticipant("SC/a", "a"), _PanelParticipant("SC/b", "b")]
    with pytest.raises(ValueError, match="requires a moderator"):
        await convene("q", panel, mode=ConversationMode.SYMPOSIUM, topic_id="t", post=post)


async def test_colloquy_runs_opening_plus_two_critique_rounds_no_synthesis() -> None:
    a = _PanelParticipant("SC/a", "a")
    b = _PanelParticipant("SC/b", "b")
    posted, post = _recording_sink()

    transcript = await convene("q", [a, b], mode=ConversationMode.COLLOQUY, topic_id="t", post=post)

    assert transcript.mode is ConversationMode.COLLOQUY
    assert transcript.stopped == STOPPED_COMPLETED
    assert transcript.synthesis is None
    # Opening fan-out + two critique rounds = three rounds, each with both participants.
    assert len(transcript.rounds) == 3
    assert all(len(r) == 2 for r in transcript.rounds)
    assert len(posted) == 6


async def test_roundtable_runs_one_critique_round() -> None:
    panel = [_PanelParticipant(f"SC/{i}", str(i)) for i in range(3)]
    _posted, post = _recording_sink()

    transcript = await convene(
        "q", panel, mode=ConversationMode.ROUNDTABLE, topic_id="t", post=post
    )

    assert len(transcript.rounds) == 2
    assert transcript.synthesis is None


async def test_opening_round_has_no_peer_panel_critique_round_does() -> None:
    a = _PanelParticipant("SC/a", "alpha")
    b = _PanelParticipant("SC/b", "beta")
    _posted, post = _recording_sink()

    await convene(
        "q",
        [a, b],
        mode=ConversationMode.ROUNDTABLE,
        topic_id="t",
        post=post,
        shared_context="rules",
    )

    # Opening: shared context only.
    assert a.contexts[0] == "rules"
    assert PEER_FENCE not in a.contexts[0]
    # Critique round: shared context + directive + the panel's opening answers as fenced data.
    assert "rules" in a.contexts[1]
    assert CRITIQUE_DIRECTIVE in a.contexts[1]
    assert PEER_FENCE in a.contexts[1]
    # Each participant sees the other's opening answer.
    assert "beta-1" in a.contexts[1]
    assert "alpha-1" in b.contexts[1]


async def test_symposium_synthesises_with_the_moderator() -> None:
    panel = [_PanelParticipant("SC/a", "a"), _PanelParticipant("SC/b", "b")]
    moderator = _PanelParticipant("SC/chair", "synthesis")
    posted, post = _recording_sink()

    transcript = await convene(
        "q",
        panel,
        mode=ConversationMode.SYMPOSIUM,
        topic_id="t",
        post=post,
        moderator=moderator,
    )

    # Opening + one critique = two panel rounds, then the moderator's synthesis.
    assert len(transcript.rounds) == 2
    assert transcript.synthesis is not None
    assert transcript.synthesis["participant"] == "SC/chair"
    assert posted[-1]["participant"] == "SC/chair"
    # The moderator saw the synthesis directive and the panel's final answers as data.
    assert SYNTHESIS_DIRECTIVE in moderator.contexts[0]
    assert PEER_FENCE in moderator.contexts[0]


async def test_budget_halts_before_critique_and_skips_synthesis() -> None:
    panel = [_PanelParticipant("SC/a", "a", cost=0.5), _PanelParticipant("SC/b", "b", cost=0.5)]
    moderator = _PanelParticipant("SC/chair", "s", cost=0.5)
    posted, post = _recording_sink()

    transcript = await convene(
        "q",
        panel,
        mode=ConversationMode.SYMPOSIUM,
        topic_id="t",
        post=post,
        moderator=moderator,
        budget_usd=0.9,
    )

    # Opening alone costs 1.0 >= 0.9, so no critique round and no synthesis run.
    assert transcript.stopped == STOPPED_BUDGET
    assert len(transcript.rounds) == 1
    assert transcript.synthesis is None
    assert moderator.contexts == []
    assert len(posted) == 2


async def test_budget_halts_after_a_critique_round() -> None:
    panel = [_PanelParticipant("SC/a", "a", cost=0.3), _PanelParticipant("SC/b", "b", cost=0.3)]
    _posted, post = _recording_sink()

    transcript = await convene(
        "q",
        panel,
        mode=ConversationMode.COLLOQUY,
        topic_id="t",
        post=post,
        budget_usd=1.0,
    )

    # Opening 0.6 < 1.0; after first critique 1.2 >= 1.0 → stop, second critique never runs.
    assert transcript.stopped == STOPPED_BUDGET
    assert len(transcript.rounds) == 2


async def test_total_cost_includes_synthesis() -> None:
    panel = [_PanelParticipant("SC/a", "a", cost=0.1), _PanelParticipant("SC/b", "b", cost=0.1)]
    moderator = _PanelParticipant("SC/chair", "s", cost=0.2)
    _posted, post = _recording_sink()

    transcript = await convene(
        "q",
        panel,
        mode=ConversationMode.SYMPOSIUM,
        topic_id="t",
        post=post,
        moderator=moderator,
    )

    # Two rounds x two participants x 0.1 + synthesis 0.2 = 0.6.
    assert abs(transcript.total_cost_usd - 0.6) < 1e-9
