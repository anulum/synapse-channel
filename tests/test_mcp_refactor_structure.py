# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP module split compatibility regressions

from __future__ import annotations

import asyncio
from typing import Any

import synapse_channel.mcp.bridge as bridge_module
import synapse_channel.mcp.registration as registration_module
import synapse_channel.mcp.server as compatibility_module
import synapse_channel.mcp.stdio as stdio_module

EXPECTED_TOOLS = {
    "synapse_claim",
    "synapse_release",
    "synapse_send",
    "synapse_handoff",
    "synapse_task_declare",
    "synapse_task_update",
    "synapse_board",
    "synapse_state",
    "synapse_manifest",
    "synapse_directory",
    "synapse_route_task",
}

EXPECTED_RESOURCES = {
    "synapse://board",
    "synapse://state",
    "synapse://manifest",
    "synapse://directory",
}


def test_server_reexports_refactored_mcp_symbols() -> None:
    assert compatibility_module.SynapseHubBridge is bridge_module.SynapseHubBridge
    assert compatibility_module.AgentFactory is bridge_module.AgentFactory
    assert compatibility_module.Matcher is bridge_module.Matcher
    assert compatibility_module.Sender is bridge_module.Sender
    assert compatibility_module.DEFAULT_BRIDGE_NAME is bridge_module.DEFAULT_BRIDGE_NAME
    assert compatibility_module.DEFAULT_REQUEST_TIMEOUT is bridge_module.DEFAULT_REQUEST_TIMEOUT

    assert compatibility_module._require_fastmcp is registration_module._require_fastmcp
    assert compatibility_module.build_mcp_server is registration_module.build_mcp_server
    assert compatibility_module.MCP_EXTRA_HINT is registration_module.MCP_EXTRA_HINT

    assert compatibility_module.serve_stdio is stdio_module.serve_stdio


async def test_build_mcp_server_keeps_tool_and_resource_contract() -> None:
    bridge = bridge_module.SynapseHubBridge(request_timeout=0.05)
    server = registration_module.build_mcp_server(bridge)

    assert {tool.name for tool in await server.list_tools()} == EXPECTED_TOOLS
    assert {str(resource.uri) for resource in await server.list_resources()} == EXPECTED_RESOURCES


async def test_bridge_waiter_storage_and_resolution_contract() -> None:
    bridge = bridge_module.SynapseHubBridge(request_timeout=0.05)
    assert isinstance(bridge._waiters, list)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    bridge._waiters.append((lambda data: data.get("type") == "ready", future))

    await bridge.on_message({"type": "ignored"})
    assert not future.done()
    assert isinstance(bridge._waiters, list)

    await bridge.on_message({"type": "ready", "payload": "ok"})
    assert future.result()["payload"] == "ok"
    assert bridge._waiters == []
