# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP module split compatibility regressions

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import synapse_channel.mcp.advisory_actions as advisory_actions_module
import synapse_channel.mcp.bridge as bridge_module
import synapse_channel.mcp.claim_actions as claim_actions_module
import synapse_channel.mcp.plan_actions as plan_actions_module
import synapse_channel.mcp.registration as registration_module
import synapse_channel.mcp.server as compatibility_module
import synapse_channel.mcp.stdio as stdio_module

EXPECTED_TOOLS = {
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
}

EXPECTED_RESOURCES = {
    "synapse://board",
    "synapse://state",
    "synapse://manifest",
    "synapse://directory",
}

EXPECTED_RESOURCE_TEMPLATES = {
    "synapse://agent/{agent}",
    "synapse://resource-kind/{kind}",
    "synapse://task/{task_id}",
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

    bridge = bridge_module.SynapseHubBridge(request_timeout=0.05)
    assert isinstance(bridge.claim_actions, claim_actions_module.McpClaimActions)
    assert isinstance(bridge.plan_actions, plan_actions_module.McpPlanActions)
    assert isinstance(bridge.advisory_actions, advisory_actions_module.McpAdvisoryActions)


def test_bridge_module_stays_under_godfile_hygiene_budget() -> None:
    """SCH-H-NEW-08 residual: bridge must not re-grow past the claim-split budget."""
    source = Path(bridge_module.__file__).read_text(encoding="utf-8")
    line_count = len(source.splitlines())
    # Pre-split residual was 680L; plan+advisory extraction targets <500.
    assert line_count < 500, f"mcp/bridge.py grew to {line_count} lines"


async def test_build_mcp_server_keeps_tool_and_resource_contract() -> None:
    bridge = bridge_module.SynapseHubBridge(request_timeout=0.05)
    server = registration_module.build_mcp_server(bridge)

    live_tools = {tool.name for tool in await server.list_tools()}
    assert live_tools == EXPECTED_TOOLS
    # Doctor inventory is a static frozenset — pin it to the built server so
    # "claim tools registered" cannot silently drift (SCH-H-NEW-03 fidelity).
    assert registration_module.registered_mcp_tool_names() == frozenset(live_tools)
    assert {str(resource.uri) for resource in await server.list_resources()} == EXPECTED_RESOURCES
    assert {
        template.uriTemplate for template in await server.list_resource_templates()
    } == EXPECTED_RESOURCE_TEMPLATES


async def test_registered_mcp_tool_names_matches_built_server() -> None:
    """Drift-guard: registered_mcp_tool_names() must equal build_mcp_server tools.

    The doctor posture check reads the static inventory without starting MCP;
    without this equality gate the REQUIRED_MCP_CLAIM_TOOLS subset check is
    tautological against a hand-maintained list that can diverge from @server.tool.
    """
    bridge = bridge_module.SynapseHubBridge(request_timeout=0.05)
    server = registration_module.build_mcp_server(bridge)
    live = {tool.name for tool in await server.list_tools()}
    inventory = registration_module.registered_mcp_tool_names()
    assert inventory == frozenset(live)
    assert registration_module.REQUIRED_MCP_CLAIM_TOOLS <= inventory


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
