# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from hub_e2e_helpers import running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.inbox import McpFeedInbox
from synapse_channel.mcp.server import (
    MCP_EXTRA_HINT,
    SynapseHubBridge,
    _require_fastmcp,
    build_mcp_server,
)


def test_require_fastmcp_returns_class() -> None:
    cls = _require_fastmcp()
    assert cls.__name__ == "FastMCP"


def test_require_fastmcp_missing_raises() -> None:
    def missing_fastmcp(_name: str) -> object:
        raise ImportError("mcp extra unavailable")

    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        _require_fastmcp(import_module=missing_fastmcp)


async def test_build_registers_tools_and_resources() -> None:
    server = build_mcp_server(SynapseHubBridge(request_timeout=0.05))
    tool_names = {tool.name for tool in await server.list_tools()}
    assert {
        "synapse_claim",
        "synapse_git_claim",
        "synapse_release",
        "synapse_send",
        "synapse_inbox",
        "synapse_handoff",
        "synapse_task_declare",
        "synapse_task_update",
        "synapse_board",
        "synapse_status",
        "synapse_state",
        "synapse_manifest",
        "synapse_directory",
        "synapse_route_task",
        "synapse_memory_recall",
        "synapse_resource_bids",
    } <= tool_names
    resource_uris = {str(resource.uri) for resource in await server.list_resources()}
    assert any("board" in uri for uri in resource_uris)
    assert any("state" in uri for uri in resource_uris)
    assert any("manifest" in uri for uri in resource_uris)
    assert any("directory" in uri for uri in resource_uris)
    template_uris = {template.uriTemplate for template in await server.list_resource_templates()}
    assert "synapse://task/{task_id}" in template_uris
    assert "synapse://agent/{agent}" in template_uris
    assert "synapse://resource-kind/{kind}" in template_uris


async def test_every_tool_and_resource_wrapper_dispatches(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append(EventKind.FINDING, {"statement": "MCP memory recall"}, ts=1.0)
    store.close()
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, request_timeout=0.5)
        handle.bridge.inbox_reader = McpFeedInbox(
            handle.bridge.name,
            feed_path=tmp_path / "missing-feed.ndjson",
            cursor_path=tmp_path / "inbox.cursor",
        )
        server = build_mcp_server(handle.bridge)
        try:
            await server.call_tool("synapse_claim", {"task_id": "T", "paths": ["a"]})
            await server.call_tool(
                "synapse_git_claim",
                {"task_id": "G", "paths": ["tests/test_mcp_server_build.py"]},
            )
            await server.call_tool("synapse_release", {"task_id": "T"})
            await server.call_tool("synapse_release", {"task_id": "G"})
            await server.call_tool("synapse_send", {"target": "X", "message": "m"})
            inbox = await server.call_tool("synapse_inbox", {"limit": 3})
            await server.call_tool("synapse_handoff", {"task_id": "T", "to_agent": "Y"})
            await server.call_tool("synapse_task_declare", {"task_id": "T", "title": "t"})
            await server.call_tool("synapse_task_update", {"task_id": "T", "status": "done"})
            board = await server.call_tool("synapse_board", {})
            status = await server.call_tool("synapse_status", {})
            state = await server.call_tool("synapse_state", {})
            manifest = await server.call_tool("synapse_manifest", {})
            directory = await server.call_tool("synapse_directory", {})
            route = await server.call_tool(
                "synapse_route_task",
                {"task_id": "T", "limit": 3, "include_zero": True, "event_store": None},
            )
            memory = await server.call_tool(
                "synapse_memory_recall",
                {"event_store": str(tmp_path / "events.db"), "query": "memory", "limit": 3},
            )
            bids = await server.call_tool(
                "synapse_resource_bids",
                {"task_id": "T", "limit": 3, "include_zero": True},
            )
            board_resource = await server.read_resource("synapse://board")
            state_resource = await server.read_resource("synapse://state")
            manifest_resource = await server.read_resource("synapse://manifest")
            directory_resource = await server.read_resource("synapse://directory")
            task_resource = await server.read_resource("synapse://task/T")
            agent_resource = await server.read_resource("synapse://agent/me")
            kind_resource = await server.read_resource("synapse://resource-kind/llm")
        finally:
            await handle.close()
    assert "T" in str(board)
    assert "local relay feed is missing" in str(inbox)
    assert "mailbox_pending" in str(status)
    assert "active_claims" in str(state)
    assert "[]" in str(manifest)
    assert "trust_boundary" in str(directory)
    assert "task_id" in str(route)
    assert "trust_boundary" in str(memory)
    assert "trust_boundary" in str(bids)
    assert "T" in str(board_resource)
    assert "active_claims" in str(state_resource)
    assert "[]" in str(manifest_resource)
    assert "trust_boundary" in str(directory_resource)
    assert "task_id" in str(task_resource)
    assert "capability_card" in str(agent_resource)
    assert "resources" in str(kind_resource)
    assert hub.blackboard.tasks["T"].status == "done"


async def test_build_requires_mcp_extra() -> None:
    def missing_fastmcp() -> NoReturn:
        raise RuntimeError(MCP_EXTRA_HINT)

    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        build_mcp_server(SynapseHubBridge(request_timeout=0.05), fastmcp_loader=missing_fastmcp)
    assert "synapse-channel[mcp]" in MCP_EXTRA_HINT
