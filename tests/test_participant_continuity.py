# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for session continuity across turns
"""Tests for :mod:`synapse_channel.participants.continuity`."""

from __future__ import annotations

from synapse_channel.participants.continuity import ContinuitySeat
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.participant import ParticipantChannel, ParticipantHealth


class _RecordingParticipant:
    """Records each request and returns a scripted, per-turn result."""

    def __init__(self, identity: str, sessions: list[str], *, is_error: bool = False) -> None:
        self._identity = identity
        self._sessions = sessions
        self._is_error = is_error
        self.seen: list[TurnRequest] = []

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def channel(self) -> ParticipantChannel:
        return ParticipantChannel.HEADLESS

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.seen.append(request)
        session = self._sessions[len(self.seen) - 1]
        return TurnResult(
            kind="participant.turn_result",
            participant=self._identity,
            channel="headless",
            topic_id=request.topic_id,
            answer="" if self._is_error else "ok",
            rationale="",
            abstained=False,
            is_error=self._is_error,
            reason="boom" if self._is_error else "",
            session=session,
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
            detail="recording",
        )


def _request(resume: str = "") -> TurnRequest:
    return TurnRequest(topic_id="t", prompt="q", context="c", resume_session=resume)


async def test_first_turn_has_no_resume_then_later_turns_resume() -> None:
    inner = _RecordingParticipant("SC/claude-a", ["sess-1", "sess-2"])
    seat = ContinuitySeat(inner)

    await seat.take_turn(_request())
    assert inner.seen[0].resume_session == ""

    await seat.take_turn(_request())
    assert inner.seen[1].resume_session == "sess-1"
    assert seat.session == "sess-2"


async def test_remembered_session_overrides_request_resume() -> None:
    inner = _RecordingParticipant("SC/claude-a", ["sess-1", "sess-2"])
    seat = ContinuitySeat(inner)

    await seat.take_turn(_request())
    # Even if a caller sets a resume token, the seat's memory wins.
    await seat.take_turn(_request(resume="caller-supplied"))
    assert inner.seen[1].resume_session == "sess-1"


async def test_initial_session_is_used_on_the_first_turn() -> None:
    inner = _RecordingParticipant("SC/claude-a", ["sess-9"])
    seat = ContinuitySeat(inner, session="resume-me")

    await seat.take_turn(_request())
    assert inner.seen[0].resume_session == "resume-me"


async def test_error_turn_does_not_overwrite_a_good_session() -> None:
    # An erroring turn must not clobber an already-remembered session.
    inner = _RecordingParticipant("SC/claude-a", ["sess-err"], is_error=True)
    seat = ContinuitySeat(inner, session="sess-1")
    result = await seat.take_turn(_request())
    assert result["is_error"] is True
    assert seat.session == "sess-1"
    # The errored turn still resumed the remembered session.
    assert inner.seen[0].resume_session == "sess-1"


async def test_empty_session_result_keeps_previous() -> None:
    inner = _RecordingParticipant("SC/claude-a", ["sess-1", ""])
    seat = ContinuitySeat(inner)
    await seat.take_turn(_request())
    await seat.take_turn(_request())
    assert seat.session == "sess-1"


async def test_reset_clears_memory() -> None:
    inner = _RecordingParticipant("SC/claude-a", ["sess-1", "sess-2"])
    seat = ContinuitySeat(inner)
    await seat.take_turn(_request())
    seat.reset()
    assert seat.session == ""
    await seat.take_turn(_request())
    assert inner.seen[1].resume_session == ""


def test_identity_channel_and_health_delegate_to_inner() -> None:
    inner = _RecordingParticipant("SC/claude-a", [])
    seat = ContinuitySeat(inner)
    assert seat.identity == "SC/claude-a"
    assert seat.channel is ParticipantChannel.HEADLESS
    assert seat.health().detail == "recording"
