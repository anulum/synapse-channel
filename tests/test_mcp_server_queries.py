# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import json
from pathlib import Path

from hub_e2e_helpers import close_agents, running_hub
from mcp_server_helpers import seed_claim, seed_task, start_bridge, start_manifest_agent
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.server import SynapseHubBridge


def _seed_observation_store(path: Path) -> None:
    """Write one successful observed capability signal."""
    store = EventStore(path)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "DONE",
            "title": "Chat routing task",
            "description": "Fast chat dispatch.",
            "depends_on": [],
            "status": "done",
            "suggested_owner": "",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "DONE",
            "author": "FAST",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest -q; epistemic_status=supported",
            "posted_at": 3.0,
        },
        ts=3.0,
    )
    store.close()


async def test_board_returns_json() -> None:
    async with running_hub() as (_, uri):
        await seed_task(uri, "T1", "Build")
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.board()
        finally:
            await handle.close()
    board = json.loads(out)
    assert board["tasks"][0]["task_id"] == "T1"
    assert board["ready"] == ["T1"]


async def test_board_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.board()
    assert "did not return the board" in out


async def test_state_returns_json() -> None:
    async with running_hub() as (_, uri):
        await seed_claim(uri, "OWNER", "T1", paths=["src/a.py"])
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.state()
        finally:
            await handle.close()
    snapshot = json.loads(out)
    assert snapshot["active_claims"][0]["task_id"] == "T1"
    assert snapshot["active_claims"][0]["owner"] == "OWNER"


async def test_state_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.state()
    assert "did not return its state" in out


async def test_manifest_returns_json() -> None:
    async with running_hub() as (_, uri):
        advertiser = await start_manifest_agent(uri)
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.manifest()
        finally:
            await handle.close()
            await close_agents(advertiser)
    manifest = json.loads(out)
    assert manifest[0]["agent"] == "FAST"
    assert manifest[0]["task_classes"] == ["chat"]


async def test_directory_returns_json() -> None:
    async with running_hub() as (_, uri):
        advertiser = await start_manifest_agent(uri)
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.directory()
        finally:
            await handle.close()
            await close_agents(advertiser)
    directory = json.loads(out)
    assert directory["entries"][0]["id"] == "agent:FAST"
    assert directory["entries"][0]["task_classes"] == ["chat"]
    assert directory["entries"][0]["trust"] == "discovery-only"


async def test_route_task_returns_json() -> None:
    async with running_hub() as (_, uri):
        await seed_task(uri, "T1", "Chat routing task")
        advertiser = await start_manifest_agent(uri)
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.route_task("T1")
        finally:
            await handle.close()
            await close_agents(advertiser)
    recommendation = json.loads(out)
    assert recommendation["task_id"] == "T1"
    assert recommendation["candidates"][0]["agent"] == "FAST"
    assert recommendation["candidates"][0]["reasons"][0] == "task_class:chat"


async def test_resource_bids_returns_json() -> None:
    async with running_hub() as (_, uri):
        await seed_task(uri, "T1", "Chat routing task")
        advertiser = await start_manifest_agent(uri)
        await advertiser.agent.send_message("resource", kind="llm", name="chat-model", capacity=2)
        await advertiser.recorder.wait_for(
            lambda message: (
                message.get("type") == "resource_offered" and message.get("agent") == "FAST"
            )
        )
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.resource_bids("T1", resource_kind="llm")
        finally:
            await handle.close()
            await close_agents(advertiser)
    report = json.loads(out)
    assert report["task_id"] == "T1"
    assert report["candidates"][0]["resource_id"] == "resource:FAST:llm:chat-model"
    assert report["candidates"][0]["reasons"][0] == "resource_kind:llm"


async def test_route_task_returns_observed_evidence_json(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_observation_store(db)
    async with running_hub() as (_, uri):
        await seed_task(uri, "T1", "Chat routing task")
        advertiser = await start_manifest_agent(uri)
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.route_task("T1", event_store=str(db))
        finally:
            await handle.close()
            await close_agents(advertiser)
    recommendation = json.loads(out)
    assert "observed:chat" in recommendation["candidates"][0]["reasons"]
    assert recommendation["candidates"][0]["observed_evidence"] == [
        {"seq": 2, "task_id": "DONE", "tokens": ["chat", "routing", "task"]}
    ]


async def test_manifest_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.manifest()
    assert "did not return the manifest" in out


async def test_directory_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.directory()
    assert "did not return the capability directory" in out


async def test_route_task_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.route_task("T1")
    assert "did not return semantic routing snapshots" in out


async def test_resource_bids_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.resource_bids("T1")
    assert "did not return resource bidding snapshots" in out


# --- second-reply and local-input failure branches ----------------------------


def _scripted_bridge(replies: list[dict[str, object] | None]) -> SynapseHubBridge:
    """A bridge whose reply transport plays back a fixed sequence.

    Each tool method issues its hub requests in a documented order (board,
    manifest, state); scripting the sequence reaches the branches where a LATER
    reply times out after an earlier one succeeded — a real half-answering hub.
    """
    bridge = SynapseHubBridge(request_timeout=0.05)
    queue = list(replies)

    async def playback(match: object, send: object) -> dict[str, object] | None:
        return queue.pop(0)

    bridge._await_reply = playback  # type: ignore[method-assign]
    return bridge


_BOARD_WITH_T1: dict[str, object] = {
    "board": {"tasks": [{"task_id": "T1", "title": "Build", "status": "open"}]}
}


async def test_directory_reports_a_state_reply_that_never_arrives() -> None:
    bridge = _scripted_bridge([{"manifest": []}, None])
    assert "did not return the capability directory" in await bridge.directory()


async def test_agent_resource_reports_a_state_reply_that_never_arrives() -> None:
    bridge = _scripted_bridge([{"manifest": []}, None])
    assert "did not return MCP agent resource snapshots" in await bridge.agent_resource("A")


async def test_route_task_reports_each_missing_later_reply() -> None:
    assert "did not return semantic routing snapshots" in await _scripted_bridge(
        [_BOARD_WITH_T1, None]
    ).route_task("T1")
    assert "did not return semantic routing snapshots" in await _scripted_bridge(
        [_BOARD_WITH_T1, {"manifest": []}, None]
    ).route_task("T1")


async def test_route_task_reports_a_task_absent_from_the_board() -> None:
    bridge = _scripted_bridge([{"board": {"tasks": []}}, {"manifest": []}, {"snapshot": {}}])
    assert "task 'T9' is not on the board" in await bridge.route_task("T9")


async def test_route_task_reports_a_bad_observation_store(tmp_path: Path) -> None:
    bridge = _scripted_bridge([_BOARD_WITH_T1, {"manifest": []}, {"snapshot": {}}])
    out = await bridge.route_task("T1", event_store=str(tmp_path / "absent.db"))
    assert "missing event store" in out


async def test_resource_bids_reports_each_missing_later_reply() -> None:
    assert "did not return resource bidding snapshots" in await _scripted_bridge(
        [_BOARD_WITH_T1, None]
    ).resource_bids("T1")
    assert "did not return resource bidding snapshots" in await _scripted_bridge(
        [_BOARD_WITH_T1, {"manifest": []}, None]
    ).resource_bids("T1")


async def test_resource_bids_reports_a_task_absent_from_the_board() -> None:
    bridge = _scripted_bridge([{"board": {"tasks": []}}, {"manifest": []}, {"snapshot": {}}])
    assert "task 'T9' is not on the board" in await bridge.resource_bids("T9")


async def test_memory_recall_reports_a_missing_store(tmp_path: Path) -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.memory_recall(str(tmp_path / "absent.db"), "query")
    assert "missing event store" in out
