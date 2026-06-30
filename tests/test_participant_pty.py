# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the PTY-channel participant
"""Tests for :mod:`synapse_channel.participants.pty_participant`.

A PTY participant is driven with a fake bus agent (so the relay runs without a hub) and a fake
tmux runner (so the wake injection is asserted without a real tmux). The suite proves a turn is
relayed and woken through the pane, that the peer's structured reply flows back, that the session
is only started when asked, and that health reflects the resolvable binaries.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import agent_tmux
from synapse_channel.agent_tmux import AgentTmuxConfig
from synapse_channel.participants.envelope import (
    TurnRequest,
    build_turn_result,
    turn_result_to_payload,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.pty_participant import PtyParticipant
from synapse_channel.participants.stream_json import StreamOutcome
from synapse_channel.participants.turn_relay import RelaySettings

_TOPIC = "topic-pty"
_PEER = "peer/agent"
_SENDER = "fabric/pty-relay"


def _structured_payload(answer: str = "from the pane") -> str:
    result = build_turn_result(
        participant=_PEER,
        channel=ParticipantChannel.PTY,
        request=TurnRequest(topic_id=_TOPIC, prompt="x"),
        outcome=StreamOutcome(
            answer=answer,
            rationale="",
            session_id="sp",
            is_error=False,
            subtype="success",
            cost_usd=0.0,
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
    """Captures the relay's agent and records every tmux command the wake runs."""

    def __init__(self) -> None:
        self.agent: _FakeAgent | None = None
        self.tmux_calls: list[list[str]] = []

    def factory(
        self,
        name: str,
        on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.agent = _FakeAgent(name, on_message_callback, **kwargs)
        return self.agent

    def tmux_runner(
        self,
        args: Any,
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.tmux_calls.append(list(args))
        # returncode 0 to has-session means start_session finds the session and does not
        # create one; send-keys also succeeds so inject reports injected.
        return subprocess.CompletedProcess(list(args), returncode=0, stdout="", stderr="")


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.005)


def _config(tmp_path: Path) -> AgentTmuxConfig:
    return AgentTmuxConfig(
        identity=_PEER,
        session="agent-sess",
        cwd=tmp_path,
        agent_command=("codex",),
        submit_delay=0.0,
        registry_dir=tmp_path,
    )


def _injected_prompt(harness: _Harness) -> bool:
    wanted = agent_tmux.build_wake_prompt(_PEER)
    return any("-l" in call and wanted in call for call in harness.tmux_calls)


async def test_turn_is_relayed_and_woken_through_the_pane(tmp_path: Path) -> None:
    h = _Harness()
    seat = PtyParticipant(
        config=_config(tmp_path),
        sender_identity=_SENDER,
        settings=RelaySettings(ready_timeout=1.0, reply_timeout=1.0, freetext_grace=0.05),
        agent_factory=h.factory,
        tmux_runner=h.tmux_runner,
    )
    assert seat.identity == _PEER
    assert seat.channel is ParticipantChannel.PTY
    task = asyncio.create_task(seat.take_turn(TurnRequest(topic_id=_TOPIC, prompt="say pong")))
    # The relay connected under the sender identity, published to the peer, and the wake
    # injected the fixed prompt into the pane.
    await _wait_until(lambda: h.agent is not None and bool(h.agent.sent) and _injected_prompt(h))
    assert h.agent is not None
    assert h.agent.name == _SENDER
    sent = h.agent.sent[0]
    assert sent["target"] == _PEER
    assert "participant.turn_request" in sent["payload"]
    # The session was probed before injecting (ensure_session default).
    assert any("has-session" in call for call in h.tmux_calls)
    await h.agent.deliver({"sender": _PEER, "payload": _structured_payload("from the pane")})
    result = await task
    assert result["answer"] == "from the pane"
    assert result["channel"] == ParticipantChannel.PTY.value


async def test_ensure_session_false_skips_session_start(tmp_path: Path) -> None:
    h = _Harness()
    seat = PtyParticipant(
        config=_config(tmp_path),
        sender_identity=_SENDER,
        settings=RelaySettings(ready_timeout=1.0, reply_timeout=1.0, freetext_grace=0.05),
        agent_factory=h.factory,
        tmux_runner=h.tmux_runner,
        ensure_session=False,
    )
    task = asyncio.create_task(seat.take_turn(TurnRequest(topic_id=_TOPIC, prompt="hi")))
    await _wait_until(lambda: _injected_prompt(h))
    # No session probe or creation — only the two inject send-keys calls.
    assert not any("has-session" in call for call in h.tmux_calls)
    assert not any("new-session" in call for call in h.tmux_calls)
    assert h.agent is not None
    await h.agent.deliver({"sender": _PEER, "payload": _structured_payload()})
    await task


def test_health_available_when_both_binaries_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    seat = PtyParticipant(config=_config(tmp_path), sender_identity=_SENDER)
    health = seat.health()
    assert health.available is True
    assert health.channel is ParticipantChannel.PTY
    assert "tmux at" in health.detail and "codex" in health.detail


def test_health_unavailable_when_tmux_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "tmux" else f"/usr/bin/{name}"
    )
    seat = PtyParticipant(config=_config(tmp_path), sender_identity=_SENDER)
    health = seat.health()
    assert health.available is False
    assert "tmux binary" in health.detail


def test_health_unavailable_when_agent_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/tmux" if name == "tmux" else None,
    )
    seat = PtyParticipant(config=_config(tmp_path), sender_identity=_SENDER)
    health = seat.health()
    assert health.available is False
    assert "agent binary" in health.detail
