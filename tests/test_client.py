# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from synapse_channel import client as client_module
from synapse_channel.client import DEFAULT_HUB_URI, MINIMUM_HEARTBEAT_INTERVAL, SynapseAgent


class FakeWebSocket:
    """Minimal stand-in for a websockets client connection."""

    def __init__(self, incoming: list[str]) -> None:
        self.incoming = incoming
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def __aiter__(self) -> AsyncIterator[str]:
        for message in self.incoming:
            yield message


class FakeConnect:
    """Async context manager mimicking ``websockets.asyncio.client.connect``."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *exc: object) -> None:
        return None


def _install_connection(
    monkeypatch: pytest.MonkeyPatch, websocket: FakeWebSocket
) -> None:
    monkeypatch.setattr(client_module, "connect", lambda uri: FakeConnect(websocket))


# --- construction ------------------------------------------------------------


def test_defaults_and_heartbeat_clamp() -> None:
    agent = SynapseAgent("A")
    assert agent.uri == DEFAULT_HUB_URI
    assert agent.heartbeat_interval == 20.0
    agent_fast = SynapseAgent("B", heartbeat_interval=1.0)
    assert agent_fast.heartbeat_interval == MINIMUM_HEARTBEAT_INTERVAL


# --- connect + dispatch ------------------------------------------------------


async def test_connect_registers_dispatches_and_filters_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        received.append(data)

    welcome = json.dumps({"type": "welcome", "hub_id": "syn-xyz"})
    self_echo = json.dumps({"type": "chat", "sender": "A", "payload": "mine"})
    peer = json.dumps({"type": "chat", "sender": "B", "payload": "hi"})
    bad = "this-is-not-json"
    ws = FakeWebSocket([welcome, self_echo, peer, bad])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", callback, verbose=True)
    await agent.connect()

    # Registration heartbeat was sent first.
    first = json.loads(ws.sent[0])
    assert first["type"] == "heartbeat"
    assert first["sender"] == "A"
    # Welcome populated hub id and ready state.
    assert agent.hub_id == "syn-xyz"
    assert agent.ready_event.is_set()
    # The peer message reached the callback; the self echo and bad JSON did not.
    assert [d.get("payload") for d in received if d.get("type") == "chat"] == ["hi"]
    # Listener exited cleanly.
    assert agent.running is False
    assert agent.connection is None


async def test_connect_without_callback_still_processes_welcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", None)
    await agent.connect()
    assert agent.hub_id == "h"


async def test_connect_quiet_skips_lifecycle_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    bad = "not-json"
    ws = FakeWebSocket([welcome, bad])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    out = capsys.readouterr().out
    assert "connected to Synapse" not in out
    assert "malformed JSON" not in out


async def test_connect_stops_when_running_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def callback(data: dict[str, Any]) -> None:
        seen.append(data)
        agent.running = False  # stop after the first dispatched message

    first = json.dumps({"type": "chat", "sender": "B", "payload": "1"})
    second = json.dumps({"type": "chat", "sender": "B", "payload": "2"})
    ws = FakeWebSocket([first, second])
    _install_connection(monkeypatch, ws)

    agent = SynapseAgent("A", callback)
    await agent.connect()
    assert [d["payload"] for d in seen] == ["1"]


async def test_connect_handles_connection_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(uri: str) -> FakeConnect:
        raise ConnectionRefusedError

    monkeypatch.setattr(client_module, "connect", refuse)
    agent = SynapseAgent("A", verbose=True)
    await agent.connect()
    assert "could not connect" in capsys.readouterr().out


async def test_connect_refused_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(uri: str) -> FakeConnect:
        raise ConnectionRefusedError

    monkeypatch.setattr(client_module, "connect", refuse)
    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    assert capsys.readouterr().out == ""


async def test_connect_handles_transport_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(uri: str) -> FakeConnect:
        raise OSError("reset")

    monkeypatch.setattr(client_module, "connect", boom)
    agent = SynapseAgent("A")
    await agent.connect()
    assert "Connection lost" in capsys.readouterr().out


# --- heartbeat ---------------------------------------------------------------


async def test_heartbeat_tick_noop_without_connection() -> None:
    agent = SynapseAgent("A")
    await agent._heartbeat_tick()  # no connection -> no error, nothing sent


async def test_heartbeat_tick_sends_when_connected() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent._heartbeat_tick()
    assert json.loads(ws.sent[0])["payload"] == "alive"


async def test_heartbeat_loop_runs_one_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]

    async def fake_sleep(_seconds: float) -> None:
        agent.running = False  # end the loop after the first sleep

    monkeypatch.setattr("synapse_channel.client.asyncio.sleep", fake_sleep)
    await agent._heartbeat_loop()
    assert json.loads(ws.sent[0])["payload"] == "alive"


# --- wait_until_ready --------------------------------------------------------


async def test_wait_until_ready_true_when_set() -> None:
    agent = SynapseAgent("A")
    agent.ready_event.set()
    assert await agent.wait_until_ready(timeout=0.1) is True


async def test_wait_until_ready_times_out() -> None:
    agent = SynapseAgent("A")
    assert await agent.wait_until_ready(timeout=0.1) is False


# --- send helpers ------------------------------------------------------------


async def test_send_message_noop_without_connection() -> None:
    agent = SynapseAgent("A")
    await agent.send_message("chat", payload="x")  # no connection -> silently ignored


async def test_send_helpers_emit_expected_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]

    await agent.chat("hello", target="B")
    await agent.claim("  T1  ", note="work", ttl_seconds=120.0)
    await agent.claim("T2")
    await agent.release("  T3 ")
    await agent.request_state()
    await agent.request_who()
    await agent.request_history(5)
    await agent.request_history(None)

    sent = [json.loads(raw) for raw in ws.sent]
    chat, claim_full, claim_min, release, state, who, hist_n, hist_all = sent

    assert chat == {
        "sender": "A",
        "target": "B",
        "type": "chat",
        "payload": "hello",
        "timestamp": chat["timestamp"],
    }
    assert claim_full["type"] == "claim"
    assert claim_full["task_id"] == "T1"
    assert claim_full["ttl_seconds"] == 120.0
    assert "ttl_seconds" not in claim_min
    assert release["task_id"] == "T3"
    assert state["type"] == "state_request"
    assert who["type"] == "who_request"
    assert hist_n["limit"] == 5
    assert "limit" not in hist_all


# --- start -------------------------------------------------------------------


def test_start_runs_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = {"value": False}

    async def fake_connect(self: SynapseAgent) -> None:
        ran["value"] = True

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A").start()
    assert ran["value"] is True


def test_start_swallows_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_connect(self: SynapseAgent) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A", verbose=True).start()
    assert "Shutting down" in capsys.readouterr().out


def test_start_keyboard_interrupt_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_connect(self: SynapseAgent) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(SynapseAgent, "connect", fake_connect)
    SynapseAgent("A", verbose=False).start()
    assert capsys.readouterr().out == ""


# --- scoped claim + epoch release --------------------------------------------


async def test_claim_sends_worktree_and_paths() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1", note="n", worktree="wt", paths=["src", "tests"])
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "claim"
    assert sent["worktree"] == "wt"
    assert sent["paths"] == ["src", "tests"]


async def test_claim_omits_scope_when_unset() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1")
    sent = json.loads(ws.sent[-1])
    assert "worktree" not in sent
    assert "paths" not in sent


async def test_release_sends_and_omits_epoch() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.release("T1", epoch=4)
    with_epoch = json.loads(ws.sent[-1])
    assert with_epoch["epoch"] == 4

    await agent.release("T1")
    without_epoch = json.loads(ws.sent[-1])
    assert "epoch" not in without_epoch


# --- idempotency key + resume ------------------------------------------------


async def test_claim_and_release_send_idem_key() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1", idem_key="k1")
    assert json.loads(ws.sent[-1])["idem_key"] == "k1"
    await agent.release("T1", idem_key="k2")
    assert json.loads(ws.sent[-1])["idem_key"] == "k2"


async def test_idem_key_omitted_when_unset() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1")
    assert "idem_key" not in json.loads(ws.sent[-1])


async def test_request_resume_sends_cursor() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_resume(since=7)
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "resume_request"
    assert sent["since"] == 7


async def test_update_task_sends_lifecycle_and_cas_fields() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_task(
        "T1", status="working", note="n", data_ref="r", epoch=5, expected_version=2, idem_key="k"
    )
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "task_update"
    assert sent["status"] == "working"
    assert sent["epoch"] == 5
    assert sent["expected_version"] == 2
    assert sent["idem_key"] == "k"


async def test_update_task_minimal_omits_optional_fields() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_task("T1")
    sent = json.loads(ws.sent[-1])
    assert sent["task_id"] == "T1"
    assert "status" not in sent
    assert "expected_version" not in sent


async def test_request_wait_sends_task_id() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_wait("  T1  ")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "wait_request"
    assert sent["task_id"] == "T1"


async def test_handoff_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.handoff("  T1 ", "  B ", note="over to you", epoch=3, idem_key="k")
    full = json.loads(ws.sent[-1])
    assert full["type"] == "handoff"
    assert full["task_id"] == "T1"
    assert full["to_agent"] == "B"
    assert full["note"] == "over to you"
    assert full["epoch"] == 3
    assert full["idem_key"] == "k"

    await agent.handoff("T1", "B")
    minimal = json.loads(ws.sent[-1])
    assert "note" not in minimal
    assert "epoch" not in minimal
    assert "idem_key" not in minimal


async def test_save_checkpoint_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.save_checkpoint("  T1 ", "cursor=5", epoch=2, idem_key="k")
    full = json.loads(ws.sent[-1])
    assert full["type"] == "checkpoint"
    assert full["task_id"] == "T1"
    assert full["checkpoint"] == "cursor=5"
    assert full["epoch"] == 2
    assert full["idem_key"] == "k"

    await agent.save_checkpoint("T1", "x")
    minimal = json.loads(ws.sent[-1])
    assert "epoch" not in minimal
    assert "idem_key" not in minimal


# --- shared blackboard helpers -----------------------------------------------


async def test_post_task_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.post_task(
        "  T1 ", "Parser", description="d", depends_on=["T0"], suggested_owner="FAST"
    )
    full = json.loads(ws.sent[-1])
    assert full["type"] == "ledger_task"
    assert full["task_id"] == "T1"
    assert full["title"] == "Parser"
    assert full["depends_on"] == ["T0"]
    assert full["suggested_owner"] == "FAST"

    await agent.post_task("T2", "Bare")
    minimal = json.loads(ws.sent[-1])
    assert "description" not in minimal
    assert "depends_on" not in minimal
    assert "suggested_owner" not in minimal


async def test_update_ledger_task_sends_status_and_owner() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_ledger_task("T1", status="done", suggested_owner="")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "ledger_task_update"
    assert sent["status"] == "done"
    assert sent["suggested_owner"] == ""

    await agent.update_ledger_task("T1")
    bare = json.loads(ws.sent[-1])
    assert "status" not in bare
    assert "suggested_owner" not in bare


async def test_post_progress_sends_kind_and_text() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.post_progress("  T1 ", "blocked on review", kind="blocked")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "ledger_progress"
    assert sent["task_id"] == "T1"
    assert sent["kind"] == "blocked"
    assert sent["payload"] == "blocked on review"


async def test_request_board_sends_board_request() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_board()
    assert json.loads(ws.sent[-1])["type"] == "board_request"


# --- connect authentication --------------------------------------------------


async def test_connect_sends_token_on_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A", token="s3cret", verbose=False)
    await agent.connect()
    first = json.loads(ws.sent[0])
    assert first["type"] == "heartbeat"
    assert first["token"] == "s3cret"


async def test_connect_omits_token_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    welcome = json.dumps({"type": "welcome", "hub_id": "h"})
    ws = FakeWebSocket([welcome])
    _install_connection(monkeypatch, ws)
    agent = SynapseAgent("A", verbose=False)
    await agent.connect()
    assert "token" not in json.loads(ws.sent[0])
