# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the bus-mediated turn relay
"""Tests for :mod:`synapse_channel.participants.turn_relay`.

Every relay is driven through a fake bus agent, so the suite exercises the publish/wake/await
flow and the structured-versus-free-text hybrid correlation without a real hub. The fake agent
captures sends and exposes the relay's message callback so a test can deliver a peer reply.
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
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.participants.turn_relay import (
    DEGRADED_FREETEXT_STOP,
    RelaySettings,
    relay_turn,
)

_TOPIC = "topic-1"


def _request(prompt: str = "say pong") -> TurnRequest:
    return TurnRequest(topic_id=_TOPIC, prompt=prompt, context="role: tester")


def _structured_payload(answer: str = "pong", *, topic: str = _TOPIC, session: str = "s9") -> str:
    result = build_turn_result(
        participant="peer",
        channel=ParticipantChannel.MCP,
        request=TurnRequest(topic_id=topic, prompt="x"),
        outcome=StreamOutcome(
            answer=answer,
            rationale="because",
            session_id=session,
            is_error=False,
            subtype="success",
            cost_usd=0.25,
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
        ready: bool = True,
    ) -> None:
        self.name = name
        self.callback = on_message_callback
        self.uri = uri
        self.token = token
        self.running = True
        self.ready = ready
        self.sent: list[dict[str, Any]] = []
        self.connect_cancelled = False

    async def connect(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.connect_cancelled = True
            raise

    async def wait_until_ready(self, timeout: float) -> bool:
        return self.ready

    async def send_message(
        self, msg_type: str, *, target: str = "all", payload: str = "", **extra: Any
    ) -> None:
        self.sent.append({"type": msg_type, "target": target, "payload": payload, **extra})

    async def deliver(self, msg: dict[str, Any]) -> None:
        assert self.callback is not None
        await self.callback(msg)


class _Harness:
    """Captures the agent the relay builds so a test can drive its callback."""

    def __init__(self, *, ready: bool = True) -> None:
        self.agent: _FakeAgent | None = None
        self._ready = ready

    def factory(
        self,
        name: str,
        on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> Any:
        # Returns Any so the fake satisfies the AgentFactory (-> SynapseAgent) signature,
        # matching how the other bus-bound participant tests inject a fake client.
        self.agent = _FakeAgent(name, on_message_callback, ready=self._ready, **kwargs)
        return self.agent


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.005)


def _fast(**overrides: float) -> RelaySettings:
    base: dict[str, Any] = {"ready_timeout": 1.0, "reply_timeout": 1.0, "freetext_grace": 0.05}
    base.update(overrides)
    return RelaySettings(**base)


async def _run(
    harness: _Harness, *, settings: RelaySettings, wake: Any = None
) -> asyncio.Task[Any]:
    kwargs: dict[str, Any] = {
        "target": "peer",
        "participant": "peer",
        "channel": ParticipantChannel.MCP,
        "sender_identity": "me",
        "settings": settings,
        "agent_factory": harness.factory,
    }
    if wake is not None:
        kwargs["wake"] = wake
    task = asyncio.create_task(relay_turn(_request(), **kwargs))
    await _wait_until(lambda: harness.agent is not None and bool(harness.agent.sent))
    return task


async def test_structured_reply_is_returned() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast())
    assert h.agent is not None
    # The published request is a turn_request envelope addressed to the peer.
    sent = h.agent.sent[0]
    assert sent["target"] == "peer"
    assert "participant.turn_request" in sent["payload"]
    await h.agent.deliver({"sender": "peer", "payload": _structured_payload(answer="pong")})
    result = await task
    assert result["answer"] == "pong"
    assert result["rationale"] == "because"
    assert result["session"] == "s9"
    assert result["cost_usd"] == 0.25
    assert result["stop_reason"] == "end_turn"
    # The relay tore its connection down.
    assert h.agent.connect_cancelled is True


async def test_wake_hook_is_awaited_after_publishing() -> None:
    h = _Harness()
    woken = asyncio.Event()

    async def wake() -> None:
        woken.set()

    task = await _run(h, settings=_fast(), wake=wake)
    assert woken.is_set()
    assert h.agent is not None
    await h.agent.deliver({"sender": "peer", "payload": _structured_payload()})
    await task


async def test_hub_not_ready_is_error() -> None:
    h = _Harness(ready=False)
    result = await relay_turn(
        _request(),
        target="peer",
        participant="peer",
        channel=ParticipantChannel.PTY,
        sender_identity="me",
        settings=_fast(),
        agent_factory=h.factory,
    )
    assert result["is_error"] is True
    assert "not ready" in result["reason"]
    assert result["channel"] == "pty"


async def test_no_reply_times_out() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=0.05))
    result = await task
    assert result["is_error"] is True
    assert "no reply" in result["reason"]


async def test_free_text_only_falls_back_to_degraded() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=1.0, freetext_grace=0.05))
    assert h.agent is not None
    await h.agent.deliver({"sender": "peer", "payload": "  the plain answer  "})
    result = await task
    assert result["is_error"] is False
    assert result["answer"] == "the plain answer"
    assert result["stop_reason"] == DEGRADED_FREETEXT_STOP


async def test_structured_wins_when_it_arrives_within_grace() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=1.0, freetext_grace=0.5))
    assert h.agent is not None
    await h.agent.deliver({"sender": "peer", "payload": "free text first"})
    await h.agent.deliver({"sender": "peer", "payload": _structured_payload(answer="typed wins")})
    result = await task
    assert result["answer"] == "typed wins"
    assert result["stop_reason"] == "end_turn"


async def test_reply_from_other_sender_is_ignored() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=0.15))
    assert h.agent is not None
    # A message from someone other than the target must not satisfy the turn.
    await h.agent.deliver({"sender": "stranger", "payload": _structured_payload()})
    result = await task
    assert result["is_error"] is True
    assert "no reply" in result["reason"]


async def test_structured_for_other_topic_is_not_accepted() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=0.15))
    assert h.agent is not None
    # A typed result for a different topic is neither our answer nor free text.
    await h.agent.deliver({"sender": "peer", "payload": _structured_payload(topic="other")})
    result = await task
    assert result["is_error"] is True
    assert "no reply" in result["reason"]


async def test_non_string_payload_is_ignored() -> None:
    h = _Harness()
    task = await _run(h, settings=_fast(reply_timeout=0.15))
    assert h.agent is not None
    await h.agent.deliver({"sender": "peer", "payload": {"not": "a string"}})
    result = await task
    assert result["is_error"] is True
