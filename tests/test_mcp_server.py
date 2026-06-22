# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

import pytest

from synapse_channel.mcp_server import (
    MCP_EXTRA_HINT,
    AgentFactory,
    SynapseHubBridge,
    _require_fastmcp,
    build_mcp_server,
    serve_stdio,
)
from synapse_channel.protocol import MessageType


class FakeAgent:
    """A SynapseAgent stand-in that records calls instead of touching a socket."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
        takeover: bool = False,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.ready = True
        self.calls: list[tuple[Any, ...]] = []

    async def claim(self, task_id: str, *, paths: Any = (), **_kw: Any) -> None:
        self.calls.append(("claim", task_id, list(paths)))

    async def release(self, task_id: str, **_kw: Any) -> None:
        self.calls.append(("release", task_id))

    async def chat(self, payload: str, *, target: str = "all", **_kw: Any) -> None:
        self.calls.append(("chat", target, payload))

    async def handoff(self, task_id: str, to_agent: str, **_kw: Any) -> None:
        self.calls.append(("handoff", task_id, to_agent))

    async def post_task(
        self, task_id: str, *, title: str = "", depends_on: Any = (), **_kw: Any
    ) -> None:
        self.calls.append(("post_task", task_id, title, tuple(depends_on)))

    async def update_ledger_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
        **_kw: Any,
    ) -> None:
        self.calls.append(("update_ledger_task", task_id, status, suggested_owner))

    async def request_board(self) -> None:
        self.calls.append(("request_board",))

    async def request_state(self) -> None:
        self.calls.append(("request_state",))

    async def request_manifest(self) -> None:
        self.calls.append(("request_manifest",))

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self.ready

    async def connect(self) -> None:
        self.calls.append(("connect",))


def make_bridge(*, name: str = "me", request_timeout: float = 0.1) -> SynapseHubBridge:
    """Build a bridge over a FakeAgent with a short reply timeout."""
    return SynapseHubBridge(
        agent_factory=cast(AgentFactory, FakeAgent), name=name, request_timeout=request_timeout
    )


def agent_of(bridge: SynapseHubBridge) -> FakeAgent:
    """Return the bridge's fake agent, narrowing its type for call inspection."""
    assert isinstance(bridge.agent, FakeAgent)
    return bridge.agent


async def drive(
    bridge: SynapseHubBridge,
    make_coro: Callable[[], Coroutine[Any, Any, str]],
    reply: dict[str, Any] | None = None,
) -> str:
    """Start a bridge call, wait until it has sent, optionally inject a reply, and await it."""
    task = asyncio.create_task(make_coro())
    for _ in range(50):
        if agent_of(bridge).calls:
            break
        await asyncio.sleep(0)
    if reply is not None:
        await bridge.on_message(reply)
    return await task


# -- construction + callback routing -----------------------------------------


def test_constructor_wires_callback_and_name() -> None:
    bridge = make_bridge(name="adapter")
    assert bridge.name == "adapter"
    assert bridge.agent.name == "adapter"
    # The agent's callback is the bridge's response router.
    assert bridge.agent.callback == bridge.on_message


async def test_on_message_resolves_only_matching_waiter() -> None:
    bridge = make_bridge()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    bridge._waiters.append((lambda d: d.get("type") == "X", future))
    await bridge.on_message({"type": "Y"})  # no match
    assert not future.done()
    await bridge.on_message({"type": "X"})  # match resolves
    assert future.done()


async def test_on_message_no_waiters_is_noop() -> None:
    bridge = make_bridge()
    await bridge.on_message({"type": "chat"})  # nothing registered, no error
    assert bridge._waiters == []


async def test_on_message_skips_already_resolved_waiter() -> None:
    bridge = make_bridge()
    loop = asyncio.get_running_loop()
    done: asyncio.Future[dict[str, Any]] = loop.create_future()
    done.set_result({})
    bridge._waiters.append((lambda d: True, done))
    await bridge.on_message({"type": "anything"})  # matches but already done -> skipped
    assert bridge._waiters  # not removed by on_message


# -- claim --------------------------------------------------------------------


async def test_claim_granted() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"}
    out = await drive(bridge, lambda: bridge.claim("T1", ["src/a.py"]), reply)
    assert "granted" in out
    assert "src/a.py" in out
    assert ("claim", "T1", ["src/a.py"]) in agent_of(bridge).calls


async def test_claim_granted_whole_worktree() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "whole worktree" in out


async def test_claim_denied() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_DENIED, "task_id": "T1", "payload": "held by ALPHA"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "denied" in out
    assert "ALPHA" in out


async def test_claim_grant_for_other_owner_is_not_mine() -> None:
    bridge = make_bridge(name="me", request_timeout=0.05)
    # A grant addressed to a different owner must not satisfy our claim.
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "OTHER"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "no response" in out


async def test_claim_timeout() -> None:
    bridge = make_bridge(name="me", request_timeout=0.05)
    out = await bridge.claim("T1")
    assert "no response" in out


async def test_claim_ignores_reply_for_another_task() -> None:
    bridge = make_bridge(name="me")
    task = asyncio.create_task(bridge.claim("T1"))
    for _ in range(50):
        if agent_of(bridge).calls:
            break
        await asyncio.sleep(0)
    # A grant for a different task id must not satisfy our pending claim.
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "OTHER", "owner": "me"})
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    out = await task
    assert "granted" in out


# -- release ------------------------------------------------------------------


async def test_release_granted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.RELEASE_GRANTED, "task_id": "T1"}
    out = await drive(bridge, lambda: bridge.release("T1"), reply)
    assert "released 'T1'" in out
    assert ("release", "T1") in agent_of(bridge).calls


async def test_release_denied() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.RELEASE_DENIED, "task_id": "T1", "payload": "not the owner"}
    out = await drive(bridge, lambda: bridge.release("T1"), reply)
    assert "denied" in out


async def test_release_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.release("T1")
    assert "no response" in out


# -- send (fire-and-forget) ---------------------------------------------------


async def test_send_dispatches_chat() -> None:
    bridge = make_bridge()
    out = await bridge.send("ALPHA", "status?")
    assert out == "sent to ALPHA"
    assert ("chat", "ALPHA", "status?") in agent_of(bridge).calls


# -- handoff ------------------------------------------------------------------


async def test_handoff_granted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.HANDOFF_GRANTED, "task_id": "T1"}
    out = await drive(bridge, lambda: bridge.handoff("T1", "BETA"), reply)
    assert "handed off 'T1' to BETA" in out
    assert ("handoff", "T1", "BETA") in agent_of(bridge).calls


async def test_handoff_denied() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.HANDOFF_DENIED, "task_id": "T1", "payload": "BETA offline"}
    out = await drive(bridge, lambda: bridge.handoff("T1", "BETA"), reply)
    assert "denied" in out
    assert "BETA offline" in out


async def test_handoff_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.handoff("T1", "BETA")
    assert "no response" in out


# -- task declare / update ----------------------------------------------------


async def test_task_declare_posted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.LEDGER_TASK_POSTED, "task": {"task_id": "T1", "title": "Build"}}
    out = await drive(bridge, lambda: bridge.task_declare("T1", "Build", ["T0"]), reply)
    assert "declared 'T1'" in out
    assert "Build" in out
    assert ("post_task", "T1", "Build", ("T0",)) in agent_of(bridge).calls


async def test_task_declare_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.task_declare("T1", "Build")
    assert "no response" in out


async def test_task_update_updated() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.LEDGER_TASK_UPDATED, "task": {"task_id": "T1", "status": "done"}}
    out = await drive(bridge, lambda: bridge.task_update("T1", "done"), reply)
    assert "status=done" in out
    assert ("update_ledger_task", "T1", "done", None) in agent_of(bridge).calls


async def test_task_update_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.task_update("T1", "done")
    assert "no response" in out


# -- queries (board / state / manifest) --------------------------------------


async def test_board_returns_json() -> None:
    bridge = make_bridge()
    board = {"tasks": [{"task_id": "T1"}], "ready": []}
    reply = {"type": MessageType.BOARD_SNAPSHOT, "board": board}
    out = await drive(bridge, bridge.board, reply)
    assert json.loads(out) == board
    assert ("request_board",) in agent_of(bridge).calls


async def test_board_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.board()
    assert "did not return the board" in out


async def test_state_returns_json() -> None:
    bridge = make_bridge()
    snapshot = {"active_claims": [{"task_id": "T1"}]}
    reply = {"type": MessageType.STATE_SNAPSHOT, "snapshot": snapshot}
    out = await drive(bridge, bridge.state, reply)
    assert json.loads(out) == snapshot


async def test_state_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.state()
    assert "did not return its state" in out


async def test_manifest_returns_json() -> None:
    bridge = make_bridge()
    manifest = [{"agent": "ALPHA", "task_classes": ["chat"]}]
    reply = {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": manifest}
    out = await drive(bridge, bridge.manifest, reply)
    assert json.loads(out) == manifest


async def test_manifest_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.manifest()
    assert "did not return the manifest" in out


# -- FastMCP wiring -----------------------------------------------------------


def test_require_fastmcp_returns_class() -> None:
    cls = _require_fastmcp()
    assert cls.__name__ == "FastMCP"


def test_require_fastmcp_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        _require_fastmcp()


async def test_build_registers_tools_and_resources() -> None:
    server = build_mcp_server(make_bridge())
    tool_names = {tool.name for tool in await server.list_tools()}
    assert {
        "synapse_claim",
        "synapse_release",
        "synapse_send",
        "synapse_handoff",
        "synapse_task_declare",
        "synapse_task_update",
        "synapse_board",
        "synapse_state",
        "synapse_manifest",
    } <= tool_names
    resource_uris = {str(resource.uri) for resource in await server.list_resources()}
    assert any("board" in uri for uri in resource_uris)
    assert any("state" in uri for uri in resource_uris)
    assert any("manifest" in uri for uri in resource_uris)


async def test_every_tool_and_resource_wrapper_dispatches() -> None:
    bridge = make_bridge(request_timeout=0.05)
    server = build_mcp_server(bridge)
    await server.call_tool("synapse_claim", {"task_id": "T", "paths": ["a"]})
    await server.call_tool("synapse_release", {"task_id": "T"})
    await server.call_tool("synapse_send", {"target": "X", "message": "m"})
    await server.call_tool("synapse_handoff", {"task_id": "T", "to_agent": "Y"})
    await server.call_tool("synapse_task_declare", {"task_id": "T", "title": "t"})
    await server.call_tool("synapse_task_update", {"task_id": "T", "status": "done"})
    await server.call_tool("synapse_board", {})
    await server.call_tool("synapse_state", {})
    await server.call_tool("synapse_manifest", {})
    await server.read_resource("synapse://board")
    await server.read_resource("synapse://state")
    await server.read_resource("synapse://manifest")
    kinds = {call[0] for call in agent_of(bridge).calls}
    assert {
        "claim",
        "release",
        "chat",
        "handoff",
        "post_task",
        "update_ledger_task",
        "request_board",
        "request_state",
        "request_manifest",
    } <= kinds


async def test_build_requires_mcp_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        build_mcp_server(make_bridge())
    assert "synapse-channel[mcp]" in MCP_EXTRA_HINT


# -- serve_stdio --------------------------------------------------------------


class FakeServer:
    """A FastMCP stand-in whose stdio run records that it was invoked."""

    def __init__(self) -> None:
        self.ran = False

    async def run_stdio_async(self) -> None:
        self.ran = True


async def test_serve_stdio_unreachable_hub() -> None:
    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, **kwargs)
        agent.ready = False
        return agent

    rc = await serve_stdio(
        agent_factory=cast(AgentFactory, factory), server_builder=lambda _b: FakeServer()
    )
    assert rc == 1


async def test_serve_stdio_runs_until_client_closes() -> None:
    server = FakeServer()
    rc = await serve_stdio(
        agent_factory=cast(AgentFactory, FakeAgent), server_builder=lambda _b: server
    )
    assert rc == 0
    assert server.ran
