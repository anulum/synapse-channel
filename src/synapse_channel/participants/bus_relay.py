# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bind a participant exchange or conversation onto a live Synapse hub
"""Run a participant exchange or conversation over a live Synapse hub.

:func:`~synapse_channel.participants.exchange.conduct_exchange` and
:func:`~synapse_channel.participants.conversation.conduct_conversation` are pure orchestration
with an injected result sink; this module supplies the real sink. A single connected-session
helper connects one bus identity, hands the orchestration a ``post`` coroutine that publishes
each :class:`~synapse_channel.participants.envelope.TurnResult` as a chat payload carrying the
topic id, and tears the connection down afterwards. This is the seam where the Participant
Fabric meets the bus, and the proof that a participant's reasoning is posted to the shared
channel where any peer or human can read it.

The agent is created through an injected factory (defaulting to
:class:`~synapse_channel.client.agent.SynapseAgent`), so the publish path is verified in tests
with a fake agent that records sends, while the gated real smoke test drives genuine
participants against a real hub.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.convene import ConvocationTranscript, convene
from synapse_channel.participants.conversation import (
    ConversationTranscript,
    conduct_conversation,
)
from synapse_channel.participants.envelope import TurnResult, turn_result_to_payload
from synapse_channel.participants.exchange import ExchangeTranscript, conduct_exchange
from synapse_channel.participants.modes import ConversationMode
from synapse_channel.participants.participant import Participant

ResultSink = Callable[[TurnResult], Awaitable[None]]
"""Coroutine that publishes one turn result to the bus."""


class _BusPublisher:
    """Opens a connected bus session and hands out a topic-stamping result sink.

    A single owner of the connect/publish/teardown lifecycle, shared by every bus-bound
    orchestration so the connection handling lives in one place.

    Parameters
    ----------
    identity : str
        Bus identity the session publishes under.
    uri : str
        Hub WebSocket URI.
    target : str
        Recipient for published results; ``"all"`` broadcasts to the room.
    token : str or None
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory
        Factory for the bus client; injectable so tests record sends without a hub.
    ready_timeout : float
        Seconds to wait for the hub connection to become ready.
    """

    def __init__(
        self,
        identity: str,
        *,
        uri: str,
        target: str,
        token: str | None,
        agent_factory: AgentFactory,
        ready_timeout: float,
    ) -> None:
        self._identity = identity
        self._uri = uri
        self._target = target
        self._token = token
        self._agent_factory = agent_factory
        self._ready_timeout = ready_timeout

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[ResultSink | None]:
        """Yield a result sink for a live connection, or ``None`` when the hub is unreachable.

        Yields
        ------
        ResultSink or None
            A coroutine that publishes a result as a topic-stamped chat message, or ``None``
            when the connection did not become ready before ``ready_timeout``.
        """
        agent = self._agent_factory(
            self._identity, None, uri=self._uri, verbose=False, token=self._token
        )
        conn_task = asyncio.create_task(agent.connect())
        try:
            if not await agent.wait_until_ready(timeout=self._ready_timeout):
                yield None
                return

            async def post(result: TurnResult) -> None:
                await agent.send_message(
                    MessageType.CHAT,
                    target=self._target,
                    payload=turn_result_to_payload(result),
                    topic=result["topic_id"],
                )

            yield post
        finally:
            agent.running = False
            conn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await conn_task


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
        self._opener = opener
        self._reactor = reactor
        self._publisher = _BusPublisher(
            identity,
            uri=uri,
            target=target,
            token=token,
            agent_factory=agent_factory,
            ready_timeout=ready_timeout,
        )

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
            The completed transcript, or ``None`` when the hub could not be reached.
        """
        async with self._publisher.session() as post:
            if post is None:
                return None
            return await conduct_exchange(
                question,
                self._opener,
                self._reactor,
                topic_id=topic_id,
                post=post,
                shared_context=shared_context,
            )


class BusConversation:
    """Drive a multi-round conversation and publish each result to a Synapse hub.

    Parameters
    ----------
    identity : str
        Bus identity the conversation publishes under.
    participants : Sequence[Participant]
        Cycled one per round (wrap each in a
        :class:`~synapse_channel.participants.continuity.ContinuitySeat` for memory).
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
        participants: Sequence[Participant],
        *,
        uri: str = DEFAULT_HUB_URI,
        target: str = "all",
        token: str | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        ready_timeout: float = 5.0,
    ) -> None:
        self._participants = participants
        self._publisher = _BusPublisher(
            identity,
            uri=uri,
            target=target,
            token=token,
            agent_factory=agent_factory,
            ready_timeout=ready_timeout,
        )

    async def run(
        self,
        question: str,
        *,
        rounds: int,
        topic_id: str,
        shared_context: str = "",
        budget_usd: float | None = None,
    ) -> ConversationTranscript | None:
        """Connect, conduct the conversation publishing each result, then disconnect.

        Parameters
        ----------
        question : str
            The prompt put to every turn.
        rounds : int
            Maximum number of turns to run.
        topic_id : str
            Correlation id stamped on every turn and bus payload.
        shared_context : str, optional
            Common framing prepended to every turn's context.
        budget_usd : float or None, optional
            Cumulative cost ceiling that stops the conversation early.

        Returns
        -------
        ConversationTranscript or None
            The transcript, or ``None`` when the hub could not be reached.
        """
        async with self._publisher.session() as post:
            if post is None:
                return None
            return await conduct_conversation(
                question,
                self._participants,
                rounds=rounds,
                topic_id=topic_id,
                post=post,
                shared_context=shared_context,
                budget_usd=budget_usd,
            )


class BusConvocation:
    """Convene a multi-party conversation in a mode and publish each result to a Synapse hub.

    Parameters
    ----------
    identity : str
        Bus identity the convocation publishes under.
    participants : Sequence[Participant]
        The panel that answers each round.
    moderator : Participant or None, optional
        Synthesises the final answer; required for a mode that uses one.
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
        participants: Sequence[Participant],
        *,
        moderator: Participant | None = None,
        uri: str = DEFAULT_HUB_URI,
        target: str = "all",
        token: str | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        ready_timeout: float = 5.0,
    ) -> None:
        self._participants = participants
        self._moderator = moderator
        self._publisher = _BusPublisher(
            identity,
            uri=uri,
            target=target,
            token=token,
            agent_factory=agent_factory,
            ready_timeout=ready_timeout,
        )

    async def run(
        self,
        question: str,
        *,
        mode: ConversationMode,
        topic_id: str,
        shared_context: str = "",
        budget_usd: float | None = None,
    ) -> ConvocationTranscript | None:
        """Connect, convene the conversation publishing each turn, then disconnect.

        Parameters
        ----------
        question : str
            The question put to the panel.
        mode : ConversationMode
            The conversation mode to run.
        topic_id : str
            Correlation id stamped on every turn and bus payload.
        shared_context : str, optional
            Common framing prepended to every turn's context.
        budget_usd : float or None, optional
            Cumulative cost ceiling that halts the convocation early.

        Returns
        -------
        ConvocationTranscript or None
            The transcript, or ``None`` when the hub could not be reached.
        """
        async with self._publisher.session() as post:
            if post is None:
                return None
            return await convene(
                question,
                self._participants,
                mode=mode,
                topic_id=topic_id,
                post=post,
                shared_context=shared_context,
                moderator=self._moderator,
                budget_usd=budget_usd,
            )
