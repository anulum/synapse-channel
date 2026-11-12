# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — untrusted pending work response contracts
"""Reject untrusted protocol pages before exposing work or advancing caller state."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.owned_pages import read_owned_work as native_read
from synapse_channel.core.owned_work import OwnedQuery
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.owned_work import read_owned_work, validate_owned_page
from synapse_channel.mcp.registration import build_mcp_server
from test_mcp_activity_page import connected_bridge
from test_pending_index import task


def _structured_text(result: object) -> str:
    """Return the text a tool answered with, refusing a protocol error."""
    assert isinstance(result, CallToolResult), result
    assert not result.is_error, result
    assert result.structured_content is not None
    text = result.structured_content["result"]
    assert isinstance(text, str)
    return text


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"available": False},
        {"identity": "other"},
        {"hub_id": "foreign"},
        {"project": "BETA"},
        {"mode": "read"},
        {"extra": 1},
        {"has_more": 1},
        {"revision": "private"},
        {"cursor": "private"},
        {"items": None},
        {"has_more": True, "cursor": ""},
        {"items": [None]},
        {"items": [{"kind": "unknown", "key": "a" * 64, "item_version": "b" * 64}]},
        {"items": [{"kind": "tasks", "key": "a" * 64, "item_version": "private"}]},
    ],
)
async def test_real_remote_malformed_list_cannot_look_like_valid_empty_work(
    changes: dict[str, Any],
) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "test-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            result = {
                "version": 1,
                "available": True,
                "identity": "ALPHA/worker",
                "hub_id": "test-hub",
                "project": None,
                "mode": "list",
                "revision": "c" * 64,
                "cursor": "",
                "has_more": False,
                "items": [],
            } | changes
            await socket.send(
                json.dumps(
                    {
                        "type": "history_snapshot",
                        "target": "ALPHA/worker",
                        "request_id": request["request_id"],
                        "owned_work": result,
                    }
                )
            )

    async with serve(endpoint, "127.0.0.1", 0) as server:
        async with connected_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        ) as bridge:
            with pytest.raises(RuntimeError, match="caller progress unchanged"):
                await read_owned_work(bridge.agent, bridge._await_reply, OwnedQuery(bridge.name))


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "handoffs"},
        {"key": "0" * 64},
        {"item_version": "0" * 64},
        {"offset": True},
        {"offset": 1},
        {"next_offset": True},
        {"next_offset": 0},
        {"total_chars": True},
        {"total_chars": 2**63},
        {"has_more": True},
        {"content_sha256": "private"},
        {"content": None},
        {"content": "é"},
        {"content": ""},
        {"content": "truncated"},
        {"extra": 1},
    ],
)
def test_invalid_native_record_chunks_are_refused(changes: dict[str, Any]) -> None:
    board, state = Blackboard(), SynapseState()
    board.restore_task(task())
    entry = board.owned_tasks.entries["ALPHA/old"]
    query = OwnedQuery(
        "ALPHA/worker", hub_id="hub", mode="read", key=entry.key, item_version=entry.version
    )
    result = native_read(board, state, query, hub_id="hub", now=10)
    assert validate_owned_page(result, query, "hub") == result
    with pytest.raises(ValueError):
        validate_owned_page(result | changes, query, "hub")


async def test_actual_local_mcp_reads_unscoped_own_work_on_existing_socket(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="local-owned")
    hub.blackboard.restore_task(task("bare", project=""))
    try:
        async with running_hub(hub) as (_, uri):
            async with connected_bridge(uri) as bridge:
                server = build_mcp_server(bridge)
                listed = json.loads(
                    _structured_text(await server.call_tool("synapse_owned_work", {}))
                )
                assert len(listed["items"]) == 1
                item = listed["items"][0]
                response = await server.call_tool(
                    "synapse_owned_work",
                    {
                        "mode": "read",
                        "hub_id": listed["hub_id"],
                        "key": item["key"],
                        "item_version": item["item_version"],
                    },
                )
                body = json.loads(_structured_text(response))
                assert json.loads(body["content"])["task_id"] == "bare"
                assert set(hub.agent_sockets) == {"ALPHA/worker"}
    finally:
        store.close()


async def test_disconnected_and_foreign_query_cannot_create_a_connection() -> None:
    bridge = SynapseHubBridge(name="ALPHA/worker", uri="ws://127.0.0.1:1")
    with pytest.raises(ValueError, match="identity changed"):
        await read_owned_work(bridge.agent, bridge._await_reply, OwnedQuery("ALPHA/other"))
    with pytest.raises(RuntimeError, match="caller progress unchanged"):
        await read_owned_work(bridge.agent, bridge._await_reply, OwnedQuery(bridge.name))
    assert bridge.agent.connection is None


@pytest.mark.parametrize("mode", ["foreign-target", "uncorrelated", "hub-change", "legacy"])
async def test_real_response_envelope_and_hub_binding_are_required(mode: str) -> None:
    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "test-hub"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            if mode == "hub-change":
                await socket.send(json.dumps({"type": "welcome", "hub_id": "changed-hub"}))
            frame: dict[str, Any] = {
                "type": "history_snapshot",
                "target": "ALPHA/other" if mode == "foreign-target" else "ALPHA/worker",
                "request_id": "unrelated" if mode == "uncorrelated" else request["request_id"],
            }
            if mode != "legacy":
                frame["owned_work"] = {
                    "version": 1,
                    "available": True,
                    "identity": "ALPHA/worker",
                    "hub_id": "test-hub",
                    "project": None,
                    "mode": "list",
                    "revision": "c" * 64,
                    "cursor": "",
                    "has_more": False,
                    "items": [],
                }
            await socket.send(json.dumps(frame))

    async with serve(endpoint, "127.0.0.1", 0) as server:
        async with connected_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        ) as bridge:
            with pytest.raises(RuntimeError, match="caller progress unchanged"):
                await read_owned_work(bridge.agent, bridge._await_reply, OwnedQuery(bridge.name))


async def test_actual_stdio_cli_discovers_and_reads_full_owned_work(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "stdio-hub.db")
    store.append(EventKind.LEDGER_TASK, task(description="actual stdio body").as_dict())
    hub = SynapseHub(journal=store, hub_id="stdio-owned", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "synapse_channel.cli", "mcp", "--uri", uri, "--name", "ALPHA/worker"],
                env=dict(os.environ),
            )
            with (tmp_path / "stdio.stderr").open("w") as stderr:
                async with stdio_client(params, errlog=stderr) as (read, write):
                    async with ClientSession(read, write) as session:
                        assert (await session.initialize()).protocol_version
                        assert "synapse_owned_work" in {
                            tool.name for tool in (await session.list_tools()).tools
                        }
                        result = await session.call_tool("synapse_owned_work", {})
                        listed = json.loads(_structured_text(result))
                        assert listed["project"] is None and len(listed["items"]) == 1
                        item = listed["items"][0]
                        result = await session.call_tool(
                            "synapse_owned_work",
                            {
                                "mode": "read",
                                "hub_id": listed["hub_id"],
                                "key": item["key"],
                                "item_version": item["item_version"],
                            },
                        )
                        chunk = json.loads(_structured_text(result))
                        assert chunk["has_more"] is False
                        assert json.loads(chunk["content"])["description"] == "actual stdio body"
                        assert set(hub.agent_sockets) == {"ALPHA/worker"}
    finally:
        store.close()
