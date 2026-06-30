# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the Participant abstraction over a provider session
"""The uniform participant surface over one heterogeneous provider session.

A **Participant** presents one external provider session — Claude Code, Codex, Gemini,
or another agent CLI — to the bus as a single kind of peer, hiding which channel drives
it. A moderator, a peer, or a human takes a turn from any participant the same way; the
difference between an in-session MCP tool call, a headless subprocess, and a tmux pane
lives entirely behind this surface.

The abstraction is deliberately small: a participant has an :attr:`Participant.identity`
on the bus, declares its :class:`ParticipantChannel`, reports :meth:`Participant.health`,
and answers a :class:`~synapse_channel.participants.envelope.TurnRequest` with a
:class:`~synapse_channel.participants.envelope.TurnResult` from
:meth:`Participant.take_turn`. Conversation protocols are built on top of this surface,
never inside it, so the abstraction stays free of routing or moderation policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synapse_channel.participants.envelope import TurnRequest, TurnResult


class ParticipantChannel(str, Enum):
    """How a participant's provider session is driven, ranked by robustness.

    The selection policy is ``MCP > HEADLESS > PTY``: an in-session tool call is the most
    reliable, a bus-owned headless invocation with structured output is the robust default,
    and a terminal pane is the last resort. The value is a stable lowercase string so it
    survives a round trip through a JSON bus envelope.
    """

    MCP = "mcp"
    HEADLESS = "headless"
    PTY = "pty"


@dataclass(frozen=True)
class ParticipantHealth:
    """A participant's readiness snapshot.

    Attributes
    ----------
    identity : str
        The participant's bus identity.
    channel : ParticipantChannel
        The channel this participant is driven through.
    available : bool
        Whether the participant can currently take a turn (e.g. its provider binary
        resolves, or its session is live).
    detail : str
        Human-readable explanation, especially when ``available`` is false.
    """

    identity: str
    channel: ParticipantChannel
    available: bool
    detail: str


@runtime_checkable
class Participant(Protocol):
    """A provider session presented to the bus as a uniform peer.

    Implementations drive one provider through one :class:`ParticipantChannel`. The
    surface is intentionally minimal so that conversation protocols compose participants
    without depending on which channel any of them uses.
    """

    @property
    def identity(self) -> str:
        """Return the participant's bus identity (``<project>/<type>-<id>``)."""

    @property
    def channel(self) -> ParticipantChannel:
        """Return the channel this participant is driven through."""

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Take one turn and return its typed result.

        Parameters
        ----------
        request : TurnRequest
            The prompt, shared context, and optional resume token for this turn.

        Returns
        -------
        TurnResult
            The structured outcome. Implementations must convert a provider failure into
            an error result rather than raising, so a conversation is never stranded by a
            single misbehaving participant.
        """

    def health(self) -> ParticipantHealth:
        """Return a readiness snapshot without taking a turn."""
