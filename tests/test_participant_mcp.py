# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the MCP-channel participant
"""Tests for :mod:`synapse_channel.participants.mcp_participant`.

The MCP seat is the thinnest bus-mediated participant: it relays the turn with no wake, since an
MCP-reachable peer is already listening. The suite drives it with a fake bus agent and proves a
turn reaches the peer with no terminal nudge, the structured reply flows back, and the seat
reports itself reachable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.participants.envelope import (
    TurnRequest,
    build_turn_result,
    turn_result_to_payload,
)
from synapse_channel.participants.mcp_participant import McpParticipant
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.participants.turn_relay import RelaySettings

_TOPIC = "topic-mcp"
_PEER = "quantum/codex-2b40"
_SENDER = "fabric/mcp-relay"


def _structured_payload(answer: str = "mcp answer") -> str:
    result = build_turn_result(
        participant=_PEER,
        channel=ParticipantChannel.MCP,
        request=TurnRequest(topic_id=_TOPIC, prompt="x"),
        outcome=StreamOutcome(
            answer=answer,
            rationale="reasoned",
            session_id="sm",
            is_error=False,
            subtype="success",
            cost_usd=0.1,
            num_turns=1,
            stop_reason="end_turn",
        ),
    )
    return turn_result_to_payload(result)


class _FakeAgent:
    """A bus client stand-in: records sends and exposes the relay's message callback."""

    def __init__(
        self,
        name: str,
        on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        uri: str = "",
        verbose: bool = True,
        token: str | None = None,
    ) -> None:
        self.name = name
        self.callback = on_message_callback
        self.running = True
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    async def wait_until_ready(self, timeout: float) -> bool:
        return True

    async def send_message(
        self, msg_type: str, *, target: str = "all", payload: str = "", **extra: Any
    ) -> None:
        self.sent.append({"type": msg_type, "target": target, "payload": payload, **extra})

    async def deliver(self, msg: dict[str, Any]) -> None:
        assert self.callback is not None
        await self.callback(msg)


class _Harness:
    """Captures the relay's agent so a test can drive its callback."""

    def __init__(self) -> None:
        self.agent: _FakeAgent | None = None

    def factory(
        self,
        name: str,
        on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.agent = _FakeAgent(name, on_message_callback, **kwargs)
        return self.agent


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.005)


def _seat(h: _Harness) -> McpParticipant:
    return McpParticipant(
        _PEER,
        sender_identity=_SENDER,
        settings=RelaySettings(ready_timeout=1.0, reply_timeout=1.0, freetext_grace=0.05),
        agent_factory=h.factory,
    )


async def test_turn_is_relayed_without_a_wake() -> None:
    h = _Harness()
    seat = _seat(h)
    assert seat.identity == _PEER
    assert seat.channel is ParticipantChannel.MCP
    task = asyncio.create_task(seat.take_turn(TurnRequest(topic_id=_TOPIC, prompt="say pong")))
    await _wait_until(lambda: h.agent is not None and bool(h.agent.sent))
    assert h.agent is not None
    assert h.agent.name == _SENDER
    sent = h.agent.sent[0]
    assert sent["target"] == _PEER
    assert "participant.turn_request" in sent["payload"]
    await h.agent.deliver({"sender": _PEER, "payload": _structured_payload("mcp answer")})
    result = await task
    assert result["answer"] == "mcp answer"
    assert result["rationale"] == "reasoned"
    assert result["channel"] == ParticipantChannel.MCP.value


def test_health_reports_reachable() -> None:
    seat = McpParticipant(_PEER, sender_identity=_SENDER)
    health = seat.health()
    assert health.available is True
    assert health.channel is ParticipantChannel.MCP
    assert health.identity == _PEER
    assert _PEER in health.detail
