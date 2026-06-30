# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for binding a participant exchange onto the bus
"""Tests for :mod:`synapse_channel.participants.bus_relay`.

A fake agent factory stands in for the hub: it records every published payload, so the
publish path (chat type, target, topic, serialised envelope) and the connect/teardown
lifecycle are verified without a running hub or a real model.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Sequence
from typing import Any

from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.bus_relay import BusConversation, BusExchange
from synapse_channel.participants.conversation import STOPPED_COMPLETED
from synapse_channel.participants.envelope import turn_result_from_payload
from synapse_channel.participants.headless_claude import HeadlessClaudeParticipant


def _stream(answer: str) -> str:
    init = json.dumps({"type": "system", "subtype": "init", "session_id": "s"})
    result = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "session_id": "s",
            "total_cost_usd": 0.0,
            "num_turns": 1,
            "stop_reason": "end_turn",
        }
    )
    return f"{init}\n{result}\n"


def _seat(identity: str, answer: str) -> HeadlessClaudeParticipant:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, stdout=_stream(answer), stderr="")

    return HeadlessClaudeParticipant(identity, runner=runner)


class _FakeAgent:
    """Records sends; its connect blocks until cancelled, like a real client loop."""

    def __init__(
        self,
        name: str,
        on_message: Any = None,
        *,
        uri: str = "",
        verbose: bool = True,
        token: str | None = None,
        ready: bool = True,
    ) -> None:
        self.name = name
        self.uri = uri
        self.token = token
        self.running = True
        self._ready = ready
        self.sends: list[dict[str, Any]] = []
        self.connect_cancelled = False

    async def connect(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.connect_cancelled = True
            raise

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def send_message(
        self, msg_type: str, *, target: str = "all", payload: str = "", **extra: Any
    ) -> None:
        self.sends.append({"type": msg_type, "target": target, "payload": payload, "extra": extra})


def _factory(captured: list[_FakeAgent], *, ready: bool = True) -> Any:
    def make(name: str, on_message: Any = None, **kwargs: Any) -> _FakeAgent:
        agent = _FakeAgent(name, on_message, ready=ready, **kwargs)
        captured.append(agent)
        return agent

    return make


async def test_run_publishes_both_results_as_chat_with_topic() -> None:
    captured: list[_FakeAgent] = []
    exchange = BusExchange(
        "SC/relay",
        _seat("SC/claude-a", "opener"),
        _seat("SC/codex-b", "reactor"),
        target="team",
        agent_factory=_factory(captured),
    )

    transcript = await exchange.run("the question", topic_id="topic-7")

    assert transcript is not None
    assert [r["answer"] for r in transcript.turns] == ["opener", "reactor"]

    agent = captured[0]
    assert agent.name == "SC/relay"
    assert len(agent.sends) == 2
    for send in agent.sends:
        assert send["type"] == MessageType.CHAT
        assert send["target"] == "team"
        assert send["extra"]["topic"] == "topic-7"
        envelope = turn_result_from_payload(send["payload"])
        assert envelope is not None
        assert envelope["topic_id"] == "topic-7"
    opener_envelope = turn_result_from_payload(agent.sends[0]["payload"])
    reactor_envelope = turn_result_from_payload(agent.sends[1]["payload"])
    assert opener_envelope is not None and opener_envelope["answer"] == "opener"
    assert reactor_envelope is not None and reactor_envelope["answer"] == "reactor"


async def test_run_returns_none_and_publishes_nothing_when_not_ready() -> None:
    captured: list[_FakeAgent] = []
    exchange = BusExchange(
        "SC/relay",
        _seat("SC/claude-a", "opener"),
        _seat("SC/codex-b", "reactor"),
        agent_factory=_factory(captured, ready=False),
    )

    result = await exchange.run("q", topic_id="t")

    assert result is None
    assert captured[0].sends == []


async def test_run_tears_down_the_connection() -> None:
    captured: list[_FakeAgent] = []
    exchange = BusExchange(
        "SC/relay",
        _seat("SC/claude-a", "opener"),
        _seat("SC/codex-b", "reactor"),
        agent_factory=_factory(captured),
    )

    await exchange.run("q", topic_id="t")

    agent = captured[0]
    assert agent.running is False
    assert agent.connect_cancelled is True


async def test_conversation_publishes_one_result_per_round() -> None:
    captured: list[_FakeAgent] = []
    conversation = BusConversation(
        "SC/relay",
        [_seat("SC/a", "from-a"), _seat("SC/b", "from-b")],
        target="team",
        agent_factory=_factory(captured),
    )

    transcript = await conversation.run("q", rounds=3, topic_id="topic-9")

    assert transcript is not None
    assert transcript.stopped == STOPPED_COMPLETED
    agent = captured[0]
    assert len(agent.sends) == 3
    for send in agent.sends:
        assert send["type"] == MessageType.CHAT
        assert send["target"] == "team"
        assert send["extra"]["topic"] == "topic-9"
    answers = [turn_result_from_payload(s["payload"]) for s in agent.sends]
    assert [a["answer"] for a in answers if a is not None] == ["from-a", "from-b", "from-a"]


async def test_conversation_returns_none_when_not_ready() -> None:
    captured: list[_FakeAgent] = []
    conversation = BusConversation(
        "SC/relay",
        [_seat("SC/a", "x")],
        agent_factory=_factory(captured, ready=False),
    )

    result = await conversation.run("q", rounds=2, topic_id="t")

    assert result is None
    assert captured[0].sends == []


async def test_conversation_tears_down_the_connection() -> None:
    captured: list[_FakeAgent] = []
    conversation = BusConversation(
        "SC/relay",
        [_seat("SC/a", "x")],
        agent_factory=_factory(captured),
    )

    await conversation.run("q", rounds=1, topic_id="t")

    agent = captured[0]
    assert agent.running is False
    assert agent.connect_cancelled is True
