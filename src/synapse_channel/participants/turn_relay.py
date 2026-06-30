# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bus-mediated turn relay shared by the PTY and MCP channels
"""Relay one participant turn to a long-lived peer over the bus and await its reply.

The headless channel is *call-mediated*: a turn spawns a fresh process and reads the answer
from its stdout. The PTY and MCP channels are *bus-mediated*: the peer already runs, so a turn
**publishes the request to the peer over the bus** and the peer **answers over the bus**, not
through a return value. Both channels share this one relay and differ only in how they nudge
the peer to look — the injected ``wake`` hook (a PTY pane injection, or nothing for an
MCP-reachable peer).

Reply correlation is the **A + B hybrid**: the relay prefers a typed
:data:`~synapse_channel.participants.envelope.ENVELOPE_KIND` ``turn_result`` from the target,
matched by ``topic_id`` (what a peer running the responder publishes); but if only a free-text
reply from the target arrives, the relay waits a short grace for a structured one and then falls
back to wrapping that free text as a degraded answer. So a peer with the responder gives full
typed results, and a plain peer still answers — degraded, marked by a ``degraded-freetext`` stop
reason. A hub that never becomes ready, or a turn with no reply at all, becomes an error result
rather than a raised exception, so one silent peer cannot strand an orchestration.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.envelope import (
    TurnRequest,
    TurnResult,
    build_turn_result,
    error_turn_result,
    turn_request_to_payload,
    turn_result_from_payload,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.stream_json import StreamOutcome

WakeHook = Callable[[], Awaitable[None]]
"""Coroutine that nudges the peer to read the bus after the request is published.

PTY supplies a tmux pane injection; an MCP-reachable peer needs none, so :func:`no_wake` is the
default. The hook is awaited once, after the request is on the bus.
"""

DEGRADED_FREETEXT_STOP = "degraded-freetext"
"""Stop reason marking a result salvaged from a free-text reply rather than a typed envelope."""


async def no_wake() -> None:
    """Wake hook that does nothing — for a peer already reachable on the bus (e.g. MCP)."""
    return None


@dataclass(frozen=True)
class RelaySettings:
    """Connection and timing knobs for a bus-mediated turn.

    Attributes
    ----------
    uri : str
        Hub WebSocket URI.
    token : str or None
        Shared-secret token for a secured hub; ``None`` for an open hub.
    ready_timeout : float
        Seconds to wait for the hub connection to become ready.
    reply_timeout : float
        Seconds to wait for any reply from the target before giving up.
    freetext_grace : float
        After a free-text reply arrives, seconds to keep waiting for a structured
        ``turn_result`` before falling back to the free text.
    """

    uri: str = DEFAULT_HUB_URI
    token: str | None = None
    ready_timeout: float = 10.0
    reply_timeout: float = 600.0
    freetext_grace: float = 5.0


class _ReplyCorrelator:
    """Resolve a peer's reply from inbound bus messages, structured result preferred.

    Watches messages from one target identity: a payload that parses as a ``turn_result`` for
    the awaited ``topic_id`` resolves the structured future; any other text from the target is
    recorded as the latest free-text candidate for the degraded fallback.
    """

    def __init__(self, *, target: str, topic_id: str) -> None:
        self._target = target
        self._topic_id = topic_id
        self._structured: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        self._freetext = ""
        self._freetext_seen = asyncio.Event()

    async def on_message(self, msg: dict[str, Any]) -> None:
        """Inspect one inbound bus message and record a structured or free-text reply."""
        if msg.get("sender") != self._target:
            return
        payload = msg.get("payload")
        text = payload if isinstance(payload, str) else ""
        result = turn_result_from_payload(text) if text else None
        if result is not None:
            # A typed turn_result is never treated as free text; only ours resolves the future.
            if result["topic_id"] == self._topic_id and not self._structured.done():
                self._structured.set_result(result)
            return
        if text:
            self._freetext = text
            self._freetext_seen.set()

    async def _first_signal(self) -> None:
        """Return once either a structured result or the first free-text reply has arrived.

        The structured future is awaited through a shield so cancelling this waiter never
        cancels the shared future itself — :meth:`await_reply` reads it afterwards.
        """
        freetext_wait: asyncio.Task[Any] = asyncio.ensure_future(self._freetext_seen.wait())
        structured_wait: asyncio.Task[Any] = asyncio.ensure_future(asyncio.shield(self._structured))
        try:
            await asyncio.wait(
                {freetext_wait, structured_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in (freetext_wait, structured_wait):
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter

    async def await_reply(
        self,
        *,
        channel: ParticipantChannel,
        participant: str,
        request: TurnRequest,
        reply_timeout: float,
        freetext_grace: float,
    ) -> TurnResult:
        """Await the peer's reply, preferring a structured result over free text."""
        try:
            await asyncio.wait_for(self._first_signal(), reply_timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return error_turn_result(
                participant=participant,
                channel=channel,
                request=request,
                reason=f"no reply from {self._target!r} within {reply_timeout:g}s",
            )
        if self._structured.done():
            return self._structured.result()
        # Free text arrived first; give a structured result a short grace to still win.
        try:
            return await asyncio.wait_for(asyncio.shield(self._structured), freetext_grace)
        except (TimeoutError, asyncio.TimeoutError):
            return self._degraded(channel=channel, participant=participant, request=request)

    def _degraded(
        self, *, channel: ParticipantChannel, participant: str, request: TurnRequest
    ) -> TurnResult:
        """Wrap the latest free-text reply as a degraded, typed result."""
        outcome = StreamOutcome(
            answer=self._freetext,
            rationale="",
            session_id="",
            is_error=False,
            subtype="success",
            cost_usd=0.0,
            num_turns=0,
            stop_reason=DEGRADED_FREETEXT_STOP,
        )
        return build_turn_result(
            participant=participant, channel=channel, request=request, outcome=outcome
        )


async def relay_turn(
    request: TurnRequest,
    *,
    target: str,
    participant: str,
    channel: ParticipantChannel,
    sender_identity: str,
    wake: WakeHook = no_wake,
    settings: RelaySettings | None = None,
    agent_factory: AgentFactory = SynapseAgent,
) -> TurnResult:
    """Relay one turn to a peer over the bus and return its reply as a :class:`TurnResult`.

    Parameters
    ----------
    request : TurnRequest
        The turn to relay; its ``topic_id`` correlates the reply.
    target : str
        Bus identity of the peer the request is sent to and whose reply is awaited.
    participant : str
        Identity recorded on the resulting :class:`TurnResult` (normally ``target``).
    channel : ParticipantChannel
        The channel this relay serves (``PTY`` or ``MCP``), recorded on the result.
    sender_identity : str
        Bus identity this relay connects under to publish and listen.
    wake : WakeHook, optional
        Coroutine awaited once after the request is published, to nudge the peer; defaults to
        :func:`no_wake`.
    settings : RelaySettings or None, optional
        Connection and timing knobs; defaults to :class:`RelaySettings`.
    agent_factory : AgentFactory, optional
        Factory for the bus client; injectable so tests drive a relay without a hub.

    Returns
    -------
    TurnResult
        The peer's structured result, a degraded free-text result, or an error result when the
        hub is unreachable or no reply arrives in time.
    """
    settings = settings or RelaySettings()
    correlator = _ReplyCorrelator(target=target, topic_id=request.topic_id)
    agent = agent_factory(
        sender_identity,
        correlator.on_message,
        uri=settings.uri,
        verbose=False,
        token=settings.token,
    )
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=settings.ready_timeout):
            return error_turn_result(
                participant=participant,
                channel=channel,
                request=request,
                reason=f"hub at {settings.uri} not ready within {settings.ready_timeout:g}s",
            )
        await agent.send_message(
            MessageType.CHAT,
            target=target,
            payload=turn_request_to_payload(request),
            topic=request.topic_id,
        )
        await wake()
        return await correlator.await_reply(
            channel=channel,
            participant=participant,
            request=request,
            reply_timeout=settings.reply_timeout,
            freetext_grace=settings.freetext_grace,
        )
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task
