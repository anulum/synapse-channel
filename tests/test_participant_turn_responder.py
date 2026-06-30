# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the peer-side bus turn responder
"""Tests for :mod:`synapse_channel.participants.turn_responder`.

A responder is served against a fake bus agent that records sends and exposes the message
callback, with a fake inner participant returning a scripted result. The suite drives a turn
request through the callback and asserts the re-stamped result is published back to the sender,
that foreign payloads take no turn, and that an unready hub ends ``serve`` with ``False``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.participants.envelope import (
    REQUEST_KIND,
    TurnRequest,
    TurnResult,
    build_turn_result,
    turn_request_to_payload,
    turn_result_from_payload,
)
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.participants.turn_responder import (
    ResponderSettings,
    TurnResponder,
)

_TOPIC = "topic-7"
_RESPONDER = "peer/responder"


class _FakeInner:
    """A scripted participant: records the request it answered and returns a fixed result."""

    def __init__(self) -> None:
        self.identity = "inner/driver"
        self.channel = ParticipantChannel.HEADLESS
        self.seen: TurnRequest | None = None

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        self.seen = request
        return build_turn_result(
            participant=self.identity,
            channel=self.channel,
            request=request,
            outcome=StreamOutcome(
                answer="the answer",
                rationale="the reasoning",
                session_id="sess-3",
                is_error=False,
                subtype="success",
                cost_usd=0.5,
                num_turns=2,
                stop_reason="end_turn",
            ),
        )

    def health(self) -> ParticipantHealth:
        return ParticipantHealth(
            identity=self.identity, channel=self.channel, available=True, detail=""
        )


class _FakeAgent:
    """A bus client stand-in: records sends and exposes the responder's message callback."""

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
    """Captures the agent a responder builds so a test can drive its callback."""

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


def _request_payload(prompt: str = "say pong") -> str:
    return turn_request_to_payload(
        TurnRequest(topic_id=_TOPIC, prompt=prompt, context="role: tester", resume_session="s0")
    )


async def _serving(
    harness: _Harness, inner: _FakeInner
) -> tuple[asyncio.Task[bool], asyncio.Event]:
    stop = asyncio.Event()
    responder = TurnResponder(
        inner,
        identity=_RESPONDER,
        settings=ResponderSettings(ready_timeout=1.0),
        agent_factory=harness.factory,
    )
    assert responder.identity == _RESPONDER
    task = asyncio.create_task(responder.serve(stop=stop))
    await _wait_until(lambda: harness.agent is not None)
    return task, stop


async def test_request_yields_restamped_result_to_sender() -> None:
    h = _Harness()
    inner = _FakeInner()
    task, stop = await _serving(h, inner)
    assert h.agent is not None
    await h.agent.deliver({"sender": "asker", "payload": _request_payload("say pong")})
    await _wait_until(lambda: bool(h.agent and h.agent.sent))
    sent = h.agent.sent[0]
    # The reply goes back to the requester, stamped with this peer's topic id.
    assert sent["target"] == "asker"
    assert sent["topic"] == _TOPIC
    result = turn_result_from_payload(sent["payload"])
    assert result is not None
    # Re-stamped: the envelope records the responder, not the inner driver.
    assert result["participant"] == _RESPONDER
    assert result["channel"] == ParticipantChannel.MCP.value
    # The inner's substance is preserved.
    assert result["answer"] == "the answer"
    assert result["rationale"] == "the reasoning"
    assert result["session"] == "sess-3"
    assert result["cost_usd"] == 0.5
    assert result["topic_id"] == _TOPIC
    # The inner saw the parsed request verbatim.
    assert inner.seen is not None
    assert inner.seen.prompt == "say pong"
    assert inner.seen.resume_session == "s0"
    stop.set()
    assert await task is True
    assert h.agent.connect_cancelled is True


async def test_non_string_payload_takes_no_turn() -> None:
    h = _Harness()
    inner = _FakeInner()
    task, stop = await _serving(h, inner)
    assert h.agent is not None
    await h.agent.deliver({"sender": "asker", "payload": {"not": "a string"}})
    assert h.agent.sent == []
    assert inner.seen is None
    stop.set()
    assert await task is True


async def test_non_request_payload_takes_no_turn() -> None:
    h = _Harness()
    inner = _FakeInner()
    task, stop = await _serving(h, inner)
    assert h.agent is not None
    # Valid JSON, but not a turn-request envelope.
    await h.agent.deliver({"sender": "asker", "payload": json.dumps({"kind": "something.else"})})
    assert h.agent.sent == []
    assert inner.seen is None
    stop.set()
    assert await task is True


async def test_missing_sender_takes_no_turn() -> None:
    h = _Harness()
    inner = _FakeInner()
    task, stop = await _serving(h, inner)
    assert h.agent is not None
    # A genuine request, but no usable sender to reply to.
    await h.agent.deliver({"payload": _request_payload()})
    await h.agent.deliver({"sender": "", "payload": _request_payload()})
    assert h.agent.sent == []
    assert inner.seen is None
    stop.set()
    assert await task is True


async def test_unready_hub_ends_serve_false() -> None:
    h = _Harness(ready=False)
    inner = _FakeInner()
    stop = asyncio.Event()
    responder = TurnResponder(
        inner,
        identity=_RESPONDER,
        channel=ParticipantChannel.PTY,
        settings=ResponderSettings(ready_timeout=1.0),
        agent_factory=h.factory,
    )
    result = await responder.serve(stop=stop)
    assert result is False
    assert h.agent is not None
    # The teardown ran (running cleared); clean cancellation of a started connect is asserted
    # by the served path, where the connect task has had a chance to begin.
    assert h.agent.running is False


def test_request_kind_constant_is_used() -> None:
    # Guards the wire contract the responder validates against.
    assert json.loads(_request_payload())["kind"] == REQUEST_KIND
