# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the two-participant exchange
"""Tests for :mod:`synapse_channel.participants.exchange`."""

from __future__ import annotations

from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    error_turn_result,
)
from synapse_channel.participants.exchange import (
    REACTION_DIRECTIVE,
    ResultSink,
    conduct_exchange,
)
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.peer_boundary import PEER_FENCE


class _ScriptedParticipant:
    """A participant that records the request it saw and returns a fixed answer."""

    def __init__(self, identity: str, answer: str) -> None:
        self._identity = identity
        self._answer = answer
        self.seen: TurnRequest | None = None

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.seen = request
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
            cost_usd=0.0,
            stop_reason="end_turn",
            model="",
            input_tokens=0,
            output_tokens=0,
        )

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(
            identity=self._identity,
            channel=ParticipantChannel.HEADLESS,
            available=True,
            detail="scripted",
        )


class _ErroringParticipant(_ScriptedParticipant):
    """An opener whose turn fails, to check the reactor still sees framed data."""

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.seen = request
        return error_turn_result(
            participant=self._identity,
            channel=ParticipantChannel.HEADLESS,
            request=request,
            reason="provider crashed",
        )


def _recording_sink() -> tuple[list[TurnResult], ResultSink]:
    posted: list[TurnResult] = []

    async def post(result: TurnResult) -> None:
        posted.append(result)

    return posted, post


async def test_exchange_runs_both_turns_and_publishes_each_in_order() -> None:
    opener = _ScriptedParticipant("SC/claude-a", "opener says")
    reactor = _ScriptedParticipant("SC/codex-b", "reactor says")
    posted, post = _recording_sink()

    transcript = await conduct_exchange(
        "what is the plan?",
        opener,
        reactor,
        topic_id="topic-1",
        post=post,
    )

    assert transcript.topic_id == "topic-1"
    assert transcript.question == "what is the plan?"
    assert [r["answer"] for r in transcript.turns] == ["opener says", "reactor says"]
    assert [r["participant"] for r in posted] == ["SC/claude-a", "SC/codex-b"]


async def test_opener_sees_only_shared_context() -> None:
    opener = _ScriptedParticipant("SC/claude-a", "x")
    reactor = _ScriptedParticipant("SC/codex-b", "y")
    _posted, post = _recording_sink()

    await conduct_exchange(
        "q",
        opener,
        reactor,
        topic_id="t",
        post=post,
        shared_context="house rules",
    )

    assert opener.seen is not None
    assert opener.seen.context == "house rules"
    assert PEER_FENCE not in opener.seen.context


async def test_reactor_context_carries_directive_and_fenced_peer_block() -> None:
    opener = _ScriptedParticipant("SC/claude-a", "the opener answer")
    reactor = _ScriptedParticipant("SC/codex-b", "y")
    _posted, post = _recording_sink()

    await conduct_exchange(
        "q",
        opener,
        reactor,
        topic_id="t",
        post=post,
        shared_context="house rules",
    )

    assert reactor.seen is not None
    ctx = reactor.seen.context
    assert "house rules" in ctx
    assert REACTION_DIRECTIVE in ctx
    assert PEER_FENCE in ctx
    assert "the opener answer" in ctx
    # The reactor answers the same question as the opener.
    assert reactor.seen.prompt == "q"


async def test_reactor_context_without_shared_context_still_frames_peer() -> None:
    opener = _ScriptedParticipant("SC/claude-a", "ans")
    reactor = _ScriptedParticipant("SC/codex-b", "y")
    _posted, post = _recording_sink()

    await conduct_exchange(
        "q",
        opener,
        reactor,
        topic_id="t",
        post=post,
    )

    assert reactor.seen is not None
    ctx = reactor.seen.context
    assert ctx.startswith(REACTION_DIRECTIVE)
    assert PEER_FENCE in ctx


async def test_failed_opener_is_framed_for_the_reactor() -> None:
    opener = _ErroringParticipant("SC/claude-a", "ignored")
    reactor = _ScriptedParticipant("SC/codex-b", "y")
    posted, post = _recording_sink()

    transcript = await conduct_exchange(
        "q",
        opener,
        reactor,
        topic_id="t",
        post=post,
    )

    assert transcript.turns[0]["is_error"] is True
    assert reactor.seen is not None
    assert "the peer's turn failed: provider crashed" in reactor.seen.context
    # Both turns are still published.
    assert len(posted) == 2
