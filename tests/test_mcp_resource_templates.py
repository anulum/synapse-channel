# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for MCP resource templates

from __future__ import annotations

import json

from hub_e2e_helpers import close_agents, running_hub
from mcp_server_helpers import seed_task, start_bridge, start_manifest_agent
from synapse_channel.mcp.resource_views import (
    agent_resource_to_json,
    task_resource_to_json,
)
from synapse_channel.mcp.server import SynapseHubBridge, build_mcp_server


async def test_mcp_server_registers_resource_templates() -> None:
    server = build_mcp_server(SynapseHubBridge(request_timeout=0.05))

    templates = {template.uriTemplate for template in await server.list_resource_templates()}

    assert templates == {
        "synapse://agent/{agent}",
        "synapse://resource-kind/{kind}",
        "synapse://task/{task_id}",
    }


async def test_task_resource_template_returns_one_board_task() -> None:
    async with running_hub() as (_, uri):
        await seed_task(uri, "T1", "Build task resource")
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.task_resource("T1")
        finally:
            await handle.close()

    payload = json.loads(out)
    assert payload["task"]["task_id"] == "T1"
    assert payload["trust_boundary"].startswith("MCP resource templates are read-only")


async def test_agent_resource_template_returns_manifest_and_resource_offers() -> None:
    async with running_hub() as (_, uri):
        advertiser = await start_manifest_agent(uri)
        await advertiser.agent.send_message("resource", kind="llm", name="chat-model", capacity=2)
        await advertiser.recorder.wait_for(
            lambda message: (
                message.get("type") == "resource_offered" and message.get("agent") == "FAST"
            )
        )
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.agent_resource("FAST")
        finally:
            await handle.close()
            await close_agents(advertiser)

    payload = json.loads(out)
    assert payload["agent"] == "FAST"
    assert payload["capability_card"]["agent"] == "FAST"
    assert payload["resources"][0]["name"] == "chat-model"


async def test_resource_kind_template_filters_resource_offers() -> None:
    async with running_hub() as (_, uri):
        advertiser = await start_manifest_agent(uri)
        await advertiser.agent.send_message("resource", kind="llm", name="chat-model", capacity=2)
        await advertiser.agent.send_message("resource", kind="fs", name="workspace", capacity=1)
        await advertiser.recorder.wait_for(
            lambda message: (
                message.get("type") == "resource_offered" and message.get("kind") == "fs"
            )
        )
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.resource_kind_resource("llm")
        finally:
            await handle.close()
            await close_agents(advertiser)

    payload = json.loads(out)
    assert payload["kind"] == "llm"
    assert [resource["name"] for resource in payload["resources"]] == ["chat-model"]


async def test_resource_templates_report_missing_snapshots() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)

    assert "did not return MCP task resource snapshots" in await bridge.task_resource("T1")
    assert "did not return MCP agent resource snapshots" in await bridge.agent_resource("FAST")
    assert "did not return MCP resource-kind snapshots" in await bridge.resource_kind_resource(
        "llm"
    )


def test_resource_template_views_report_missing_records() -> None:
    missing_task = json.loads(task_resource_to_json({"tasks": []}, "T404"))
    missing_agent = json.loads(
        agent_resource_to_json(
            [{"agent": "OTHER"}],
            [{"agent": "OTHER", "kind": "llm", "name": "m"}],
            "FAST",
        )
    )

    assert missing_task["found"] is False
    assert missing_task["task"] == {}
    assert missing_agent["found"] is False
    assert missing_agent["capability_card"] == {}
    assert missing_agent["resources"] == []
