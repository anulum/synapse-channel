# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind a participant exchange onto a live Synapse hub
"""Run a two-participant exchange over a live Synapse hub.

:func:`~synapse_channel.participants.exchange.conduct_exchange` is pure orchestration with
an injected result sink; this module supplies the real sink. It connects one bus identity,
publishes each :class:`~synapse_channel.participants.envelope.TurnResult` as a chat payload
carrying the topic id, and tears the connection down afterwards. This is the seam where the
Participant Fabric meets the bus, and the proof that a participant's reasoning is posted to
the shared channel where any peer or human can read it.

The agent is created through an injected factory (defaulting to
:class:`~synapse_channel.client.agent.SynapseAgent`), so the publish path is verified in
tests with a fake agent that records sends, while the gated real smoke test drives genuine
participants against a real hub.
"""

from __future__ import annotations

import asyncio
import contextlib

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.envelope import TurnResult, turn_result_to_payload
from synapse_channel.participants.exchange import ExchangeTranscript, conduct_exchange
from synapse_channel.participants.participant import Participant


class BusExchange:
    """Drive a two-participant exchange and publish each result to a Synapse hub.

    Parameters
    ----------
    identity : str
        Bus identity the exchange publishes under.
    opener : Participant
        Participant that answers first.
    reactor : Participant
        Participant that reacts to the opener.
    uri : str, optional
        Hub WebSocket URI.
    target : str, optional
        Recipient for the published results; ``"all"`` broadcasts to the room.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Factory for the bus client; injectable so tests record sends without a hub.
    ready_timeout : float, optional
        Seconds to wait for the hub connection to become ready.
    """

    def __init__(
        self,
        identity: str,
        opener: Participant,
        reactor: Participant,
        *,
        uri: str = DEFAULT_HUB_URI,
        target: str = "all",
        token: str | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        ready_timeout: float = 5.0,
    ) -> None:
        self._identity = identity
        self._opener = opener
        self._reactor = reactor
        self._uri = uri
        self._target = target
        self._token = token
        self._agent_factory = agent_factory
        self._ready_timeout = ready_timeout

    async def run(
        self,
        question: str,
        *,
        topic_id: str,
        shared_context: str = "",
    ) -> ExchangeTranscript | None:
        """Connect, conduct the exchange publishing each result, then disconnect.

        Parameters
        ----------
        question : str
            The prompt put to both participants.
        topic_id : str
            Correlation id stamped on both turns and carried on each bus payload.
        shared_context : str, optional
            Common framing prepended to each participant's context.

        Returns
        -------
        ExchangeTranscript or None
            The completed transcript, or ``None`` when the hub could not be reached
            before ``ready_timeout``.
        """
        agent = self._agent_factory(
            self._identity, None, uri=self._uri, verbose=False, token=self._token
        )
        conn_task = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(timeout=self._ready_timeout):
                return None

            async def post(result: TurnResult) -> None:
                await agent.send_message(
                    MessageType.CHAT,
                    target=self._target,
                    payload=turn_result_to_payload(result),
                    topic=result["topic_id"],
                )

            return await conduct_exchange(
                question,
                self._opener,
                self._reactor,
                topic_id=topic_id,
                post=post,
                shared_context=shared_context,
            )
        finally:
            agent.running = False
            conn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await conn_task
