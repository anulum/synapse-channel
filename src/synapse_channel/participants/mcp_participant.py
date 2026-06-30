# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP-channel participant for a peer already reachable on the bus
"""Present an MCP-reachable peer as a uniform bus participant.

The ``MCP`` channel is the most robust driver and the top of the ``MCP > HEADLESS > PTY``
selection order: the peer runs with the Synapse MCP tools and its own waker, so it is already
listening on the bus and needs no external nudge to read a turn. This participant is therefore
the thinnest possible bus-mediated seat — it relays the turn with
:func:`~synapse_channel.participants.turn_relay.relay_turn` and supplies
:func:`~synapse_channel.participants.turn_relay.no_wake` as the wake hook, because the peer is
reachable without one.

The seat fronts exactly the peer named by ``target``, so its :attr:`McpParticipant.identity` is
that peer's bus identity, which the relay both addresses the request to and matches the reply by;
the relay connects under a separate ``sender_identity``. A peer running the turn responder answers
with a typed result; a peer without one still answers through the relay's degraded free-text
fallback. An unreachable hub or a silent peer becomes an error result, never a raised exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.turn_relay import RelaySettings, no_wake, relay_turn

if TYPE_CHECKING:
    from synapse_channel.cli_messaging_types import AgentFactory
    from synapse_channel.participants.envelope import TurnRequest, TurnResult


class McpParticipant:
    """An MCP-reachable peer driven over the bus as a uniform participant.

    Parameters
    ----------
    target : str
        Bus identity of the peer this seat fronts. The relay addresses the turn to it and
        matches the reply by it.
    sender_identity : str
        Bus identity the relay connects under to publish the turn and listen for the reply. It
        must differ from ``target``, since the relay cannot listen as the peer it addresses.
    settings : RelaySettings or None, optional
        Relay connection and timing knobs; defaults to :class:`RelaySettings`.
    agent_factory : AgentFactory, optional
        Factory for the relay's bus client; injectable so tests drive a turn without a hub.
    """

    def __init__(
        self,
        target: str,
        *,
        sender_identity: str,
        settings: RelaySettings | None = None,
        agent_factory: AgentFactory = SynapseAgent,
    ) -> None:
        self._target = target
        self._sender_identity = sender_identity
        self._settings = settings or RelaySettings()
        self._agent_factory = agent_factory

    @property
    def identity(self) -> str:
        """Return the bus identity of the peer this seat fronts (the relay target)."""
        return self._target

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.MCP`."""
        return ParticipantChannel.MCP

    def health(self) -> ParticipantHealth:
        """Report the seat as configured without probing the peer.

        Returns
        -------
        ParticipantHealth
            ``available`` is true: an MCP seat owns no local binary to resolve, and whether the
            peer is actually listening surfaces as an error turn when a relayed turn goes
            unanswered, rather than being guessed here.
        """
        return ParticipantHealth(
            identity=self._target,
            channel=ParticipantChannel.MCP,
            available=True,
            detail=f"mcp peer {self._target!r} reached over the bus",
        )

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Relay one turn to the MCP-reachable peer over the bus.

        Parameters
        ----------
        request : TurnRequest
            The turn to relay; its ``topic_id`` correlates the peer's reply.

        Returns
        -------
        TurnResult
            The peer's structured result, a degraded free-text result, or an error result when
            the hub is unreachable or the peer never replies. A provider failure is never raised.
        """
        return await relay_turn(
            request,
            target=self._target,
            participant=self._target,
            channel=ParticipantChannel.MCP,
            sender_identity=self._sender_identity,
            wake=no_wake,
            settings=self._settings,
            agent_factory=self._agent_factory,
        )
