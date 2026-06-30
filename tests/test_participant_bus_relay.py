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

from synapse_channel.core.accounting import USAGE_NOTE_KIND, parse_usage_note
from synapse_channel.core.protocol import MessageType
from synapse_channel.participants.auto_action import (
    AutoAction,
    AutoActionContext,
    AutoActionDispatch,
    AutoActionPolicy,
)
from synapse_channel.participants.bus_relay import (
    BusConversation,
    BusConvocation,
    BusExchange,
    BusOrchestration,
)
from synapse_channel.participants.channel_select import ProviderCapabilities
from synapse_channel.participants.conversation import STOPPED_COMPLETED
from synapse_channel.participants.envelope import turn_result_from_payload
from synapse_channel.participants.headless_claude import HeadlessClaudeParticipant
from synapse_channel.participants.modes import ConversationMode
from synapse_channel.participants.orchestration import (
    STOPPED_COMPLETED as ORCHESTRATION_COMPLETED,
)
from synapse_channel.participants.orchestration import (
    OrchestrationSeat,
)
from synapse_channel.participants.provider_route import ModelCandidate, TaskProfile
from synapse_channel.participants.session_advisor import AdvisorThresholds
from synapse_channel.participants.session_metric_note import SESSION_METRIC_NOTE_KIND


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


def _seat(identity: str, answer: str, *, model: str = "") -> HeadlessClaudeParticipant:
    def runner(args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, stdout=_stream(answer), stderr="")

    return HeadlessClaudeParticipant(identity, model=model, runner=runner)


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
        self.progress: list[dict[str, str]] = []
        self.connect_cancelled = False

    async def connect(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.connect_cancelled = True
            raise

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        # Yield once so the scheduled connect() task starts and suspends at its await,
        # matching a real client whose readiness check awaits the connection. Without this
        # the connect task may be cancelled before it ever runs, making the teardown
        # assertions race-dependent.
        await asyncio.sleep(0)
        return self._ready

    async def send_message(
        self, msg_type: str, *, target: str = "all", payload: str = "", **extra: Any
    ) -> None:
        self.sends.append({"type": msg_type, "target": target, "payload": payload, "extra": extra})

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.progress.append({"task_id": task_id, "text": text, "kind": kind})


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


async def test_no_usage_notes_posted_by_default() -> None:
    captured: list[_FakeAgent] = []
    exchange = BusExchange(
        "SC/relay",
        _seat("SC/claude-a", "opener", model="claude-opus-4-8"),
        _seat("SC/codex-b", "reactor", model="claude-opus-4-8"),
        agent_factory=_factory(captured),
    )
    await exchange.run("q", topic_id="t")
    # Emission is opt-in; the no-telemetry default posts nothing to the ledger.
    assert captured[0].progress == []


async def test_emit_usage_posts_one_accounting_note_per_turn() -> None:
    captured: list[_FakeAgent] = []
    exchange = BusExchange(
        "SC/relay",
        _seat("SC/claude-a", "opener", model="claude-opus-4-8"),
        _seat("SC/codex-b", "reactor", model="claude-opus-4-8"),
        agent_factory=_factory(captured),
        emit_usage=True,
    )
    await exchange.run("q", topic_id="topic-7")
    notes = captured[0].progress
    assert len(notes) == 2
    for note in notes:
        assert note["kind"] == USAGE_NOTE_KIND
        assert note["task_id"] == "topic-7"
        parsed = parse_usage_note(note["text"])
        assert parsed is not None
        assert parsed["model"] == "claude-opus-4-8"
        assert parsed["calls"] == 1


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


async def test_convocation_publishes_each_round_in_a_mode() -> None:
    captured: list[_FakeAgent] = []
    convocation = BusConvocation(
        "SC/relay",
        [_seat("SC/a", "from-a"), _seat("SC/b", "from-b")],
        target="team",
        agent_factory=_factory(captured),
    )

    transcript = await convocation.run("q", mode=ConversationMode.ROUNDTABLE, topic_id="topic-c")

    assert transcript is not None
    assert transcript.mode is ConversationMode.ROUNDTABLE
    agent = captured[0]
    # Roundtable: opening + one critique round, two participants each = four sends.
    assert len(agent.sends) == 4
    for send in agent.sends:
        assert send["type"] == MessageType.CHAT
        assert send["target"] == "team"
        assert send["extra"]["topic"] == "topic-c"


async def test_convocation_with_moderator_publishes_synthesis() -> None:
    captured: list[_FakeAgent] = []
    convocation = BusConvocation(
        "SC/relay",
        [_seat("SC/a", "a"), _seat("SC/b", "b")],
        moderator=_seat("SC/chair", "synthesis"),
        agent_factory=_factory(captured),
    )

    transcript = await convocation.run("q", mode=ConversationMode.SYMPOSIUM, topic_id="t")

    assert transcript is not None
    assert transcript.synthesis is not None
    # Opening + one critique (2 each) + one synthesis = five sends.
    assert len(captured[0].sends) == 5


async def test_convocation_returns_none_when_not_ready() -> None:
    captured: list[_FakeAgent] = []
    convocation = BusConvocation(
        "SC/relay",
        [_seat("SC/a", "x")],
        agent_factory=_factory(captured, ready=False),
    )

    result = await convocation.run("q", mode=ConversationMode.COLLOQUY, topic_id="t")

    assert result is None
    assert captured[0].sends == []


def _orch_seat(name: str, answer: str, *, model: str = "claude-opus-4-8") -> OrchestrationSeat:
    """Pair a scripted headless participant with an MCP-reachable routing candidate."""
    return OrchestrationSeat(
        participant=_seat(name, answer, model=model),
        candidate=ModelCandidate(
            name=name,
            model=model,
            capabilities=ProviderCapabilities(mcp_reachable=True),
        ),
    )


async def test_orchestration_publishes_one_chat_per_round() -> None:
    captured: list[_FakeAgent] = []
    orchestration = BusOrchestration(
        "SC/relay",
        [_orch_seat("SC/a", "from-a"), _orch_seat("SC/b", "from-b")],
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        target="team",
        agent_factory=_factory(captured),
    )

    transcript = await orchestration.run("q", rounds=3, topic_id="topic-o")

    assert transcript is not None
    assert transcript.stopped == ORCHESTRATION_COMPLETED
    assert len(transcript.rounds) == 3
    agent = captured[0]
    assert len(agent.sends) == 3
    for send in agent.sends:
        assert send["type"] == MessageType.CHAT
        assert send["target"] == "team"
        assert send["extra"]["topic"] == "topic-o"
        envelope = turn_result_from_payload(send["payload"])
        assert envelope is not None
        assert envelope["topic_id"] == "topic-o"
    # Durable telemetry is opt-in; the no-metrics default posts nothing to the ledger.
    assert agent.progress == []


async def test_orchestration_persists_a_durable_snapshot_per_round_when_enabled() -> None:
    captured: list[_FakeAgent] = []
    orchestration = BusOrchestration(
        "SC/relay",
        [_orch_seat("SC/a", "from-a")],
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        agent_factory=_factory(captured),
        emit_metrics=True,
    )

    transcript = await orchestration.run("q", rounds=2, topic_id="topic-m")

    assert transcript is not None
    notes = captured[0].progress
    assert len(notes) == 2
    for note in notes:
        assert note["kind"] == SESSION_METRIC_NOTE_KIND
        assert note["task_id"] == "topic-m"


async def test_orchestration_returns_none_when_not_ready() -> None:
    captured: list[_FakeAgent] = []
    orchestration = BusOrchestration(
        "SC/relay",
        [_orch_seat("SC/a", "x")],
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        agent_factory=_factory(captured, ready=False),
    )

    result = await orchestration.run("q", rounds=2, topic_id="t")

    assert result is None
    assert captured[0].sends == []
    assert captured[0].progress == []


async def test_orchestration_threads_auto_action_to_the_loop() -> None:
    captured: list[_FakeAgent] = []
    fired: list[AutoActionContext] = []

    async def _log(context: AutoActionContext) -> None:
        fired.append(context)

    dispatch = AutoActionDispatch(
        policy=AutoActionPolicy(armed=frozenset({AutoAction.LOG})),
        handlers={AutoAction.LOG: _log},
    )
    orchestration = BusOrchestration(
        "SC/relay",
        [_orch_seat("SC/a", "from-a")],
        task=TaskProfile(),
        thresholds=AdvisorThresholds(log_every_turns=1),
        agent_factory=_factory(captured),
        auto_action=dispatch,
    )

    transcript = await orchestration.run("q", rounds=2, topic_id="topic-aa")

    assert transcript is not None
    assert [r.fired_actions for r in transcript.rounds] == [
        (AutoAction.LOG,),
        (AutoAction.LOG,),
    ]
    assert len(fired) == 2
    assert all(c.action is AutoAction.LOG and c.session_id == "topic-aa" for c in fired)


async def test_orchestration_tears_down_the_connection() -> None:
    captured: list[_FakeAgent] = []
    orchestration = BusOrchestration(
        "SC/relay",
        [_orch_seat("SC/a", "x")],
        task=TaskProfile(),
        thresholds=AdvisorThresholds(),
        agent_factory=_factory(captured),
    )

    await orchestration.run("q", rounds=1, topic_id="t")

    agent = captured[0]
    assert agent.running is False
    assert agent.connect_cancelled is True
