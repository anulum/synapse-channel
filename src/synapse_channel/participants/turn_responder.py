# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — peer-side responder that answers bus-mediated turn requests
"""Answer turn requests that arrive over the bus with typed results.

A bus-mediated channel (PTY or MCP) publishes a
:class:`~synapse_channel.participants.envelope.TurnRequest` to a long-lived peer and awaits
the reply over the bus. This module is the *other side* of that exchange: the component a peer
runs so it answers those requests with a **typed**
:class:`~synapse_channel.participants.envelope.TurnResult` — option A of the relay's hybrid
correlation. Without a responder the relay still salvages a plain free-text reply (the degraded
fallback in :mod:`~synapse_channel.participants.turn_relay`); with one, a peer returns the full
structured envelope a moderator can read without re-parsing prose.

:class:`TurnResponder` wraps an inner :class:`~synapse_channel.participants.participant.Participant`
(the local provider session this peer drives) and connects one bus identity. On each inbound
:data:`~synapse_channel.participants.envelope.REQUEST_KIND` payload it runs the inner participant
and publishes the result back to the requester, **re-stamped** with the responder's own identity
and channel so the envelope records who answered on the bus rather than the inner driver. Foreign
payloads — anything that is not a turn request, or that lacks a usable sender — are ignored, so an
unrelated chat message on the bus never triggers a turn.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.envelope import (
    turn_request_from_payload,
    turn_result_to_payload,
)
from synapse_channel.participants.participant import ParticipantChannel

if TYPE_CHECKING:
    from synapse_channel.participants.participant import Participant


@dataclass(frozen=True)
class ResponderSettings:
    """Connection knobs for a peer-side responder.

    Attributes
    ----------
    uri : str
        Hub WebSocket URI.
    token : str or None
        Shared-secret token for a secured hub; ``None`` for an open hub.
    ready_timeout : float
        Seconds to wait for the hub connection to become ready before giving up.
    """

    uri: str = DEFAULT_HUB_URI
    token: str | None = None
    ready_timeout: float = 10.0


class TurnResponder:
    """Serve typed turn results for requests that arrive over the bus.

    The responder connects one bus identity and, for each turn request addressed to it, runs an
    inner participant and publishes the result back to the requester. Turns are handled one at a
    time: the bus client awaits each message callback before delivering the next, so a peer
    answers serially without overlapping its single provider session.

    Parameters
    ----------
    inner : Participant
        The local provider session this peer drives to answer a turn.
    identity : str
        Bus identity the responder connects under. A relay addresses its request to this exact
        identity and matches the reply by it, so it must equal the ``target`` the relay uses.
    channel : ParticipantChannel, optional
        Channel stamped on the published result; defaults to
        :attr:`~synapse_channel.participants.participant.ParticipantChannel.MCP`, the channel a
        responder most naturally serves.
    settings : ResponderSettings or None, optional
        Connection knobs; defaults to :class:`ResponderSettings`.
    agent_factory : AgentFactory, optional
        Factory for the bus client; injectable so tests drive the responder without a hub.
    """

    def __init__(
        self,
        inner: Participant,
        *,
        identity: str,
        channel: ParticipantChannel = ParticipantChannel.MCP,
        settings: ResponderSettings | None = None,
        agent_factory: AgentFactory = SynapseAgent,
    ) -> None:
        self._inner = inner
        self._identity = identity
        self._channel = channel
        self._settings = settings or ResponderSettings()
        self._agent_factory = agent_factory
        self._agent: SynapseAgent | None = None

    @property
    def identity(self) -> str:
        """Return the bus identity the responder answers under."""
        return self._identity

    async def _on_message(self, msg: dict[str, Any]) -> None:
        """Run the inner participant for a turn request and publish the result back.

        Non-request payloads, payloads that are not a string, and messages without a usable
        sender are ignored, so only a genuine turn request addressed at this peer takes a turn.
        """
        payload = msg.get("payload")
        if not isinstance(payload, str):
            return
        request = turn_request_from_payload(payload)
        if request is None:
            return
        sender = msg.get("sender")
        if not isinstance(sender, str) or not sender:
            return
        result = await self._inner.take_turn(request)
        # Re-stamp so the envelope records the peer that answered on the bus, not the inner
        # driver, while preserving the answer, rationale, session token, cost, and topic id.
        stamped = result.copy()
        stamped["participant"] = self._identity
        stamped["channel"] = self._channel.value
        agent = self._agent
        assert agent is not None  # set before connect, so a delivered message always has it
        await agent.send_message(
            MessageType.CHAT,
            target=sender,
            payload=turn_result_to_payload(stamped),
            topic=result["topic_id"],
        )

    async def serve(self, *, stop: asyncio.Event) -> bool:
        """Connect, answer turn requests until ``stop`` is set, then disconnect.

        Parameters
        ----------
        stop : asyncio.Event
            Serving runs until this event is set; setting it from another task ends the loop and
            tears the connection down.

        Returns
        -------
        bool
            ``True`` once the responder served and shut down cleanly; ``False`` when the hub
            never became ready within :attr:`ResponderSettings.ready_timeout`.
        """
        agent = self._agent_factory(
            self._identity,
            self._on_message,
            uri=self._settings.uri,
            verbose=False,
            token=self._settings.token,
        )
        self._agent = agent
        conn_task = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(timeout=self._settings.ready_timeout):
                return False
            await stop.wait()
            return True
        finally:
            agent.running = False
            conn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await conn_task
