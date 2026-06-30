# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — session continuity across a participant's turns
"""Give a participant memory across bus turns by threading its provider session.

A provider session is turn-based: each turn is a fresh invocation that, on its own, forgets
the last. The provider exposes continuity through a resume token — Claude's ``--resume
<session_id>`` — and every :class:`~synapse_channel.participants.envelope.TurnResult` already
carries the ``session`` the turn ran under. :class:`ContinuitySeat` is a thin decorator that
turns those two facts into memory: it is itself a
:class:`~synapse_channel.participants.participant.Participant`, so it composes anywhere one is
expected, but it remembers the last successful session and resumes it on the next turn.

The wrapped participant must keep its provider sessions on disk for the resume to find them
(for the headless driver, construct it with ``persist_session=True``); the seat threads the
token but does not configure persistence. An errored or empty turn does not overwrite a good
session, so a transient failure mid-conversation does not sever the thread.
"""

from __future__ import annotations

from dataclasses import replace

from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.participant import (
    Participant,
    ParticipantChannel,
    ParticipantHealth,
)


class ContinuitySeat:
    """A participant decorator that carries one provider session across turns.

    Parameters
    ----------
    inner : Participant
        The participant to give continuity to. For real resumption its provider sessions
        must persist (e.g. ``HeadlessClaudeParticipant(..., persist_session=True)``).
    session : str, optional
        An initial session token to resume from; empty starts a fresh thread.
    """

    def __init__(self, inner: Participant, *, session: str = "") -> None:
        self._inner = inner
        self._session = session

    @property
    def identity(self) -> str:
        """Return the wrapped participant's bus identity."""
        return self._inner.identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return the wrapped participant's channel."""
        return self._inner.channel

    @property
    def session(self) -> str:
        """Return the session token the next turn will resume, or ``""`` when fresh."""
        return self._session

    def reset(self) -> None:
        """Forget the remembered session so the next turn starts a new thread."""
        self._session = ""

    def health(self) -> ParticipantHealth:
        """Return the wrapped participant's health snapshot."""
        return self._inner.health()

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Take one turn, resuming the remembered session and recording the new one.

        The remembered session takes precedence over any ``resume_session`` already on the
        request, so the seat is the single owner of this conversation's thread. A successful
        turn that returns a session updates the memory; an error or a turn that returns no
        session leaves the previous session intact.

        Parameters
        ----------
        request : TurnRequest
            The turn to run; its ``resume_session`` is overridden by the seat's memory when
            the seat holds one.

        Returns
        -------
        TurnResult
            The wrapped participant's result, unmodified.
        """
        resumed = replace(request, resume_session=self._session or request.resume_session)
        result = await self._inner.take_turn(resumed)
        if not result["is_error"] and result["session"]:
            self._session = result["session"]
        return result
