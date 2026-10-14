# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP and A2A submission through an actual hub connection
"""Exercise MCP and A2A API submission to one recipient on a live hub."""

from __future__ import annotations

import asyncio
from typing import Any

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.mcp.bridge import SynapseHubBridge


async def test_mcp_send_and_a2a_message_reach_same_target() -> None:
    """Both production APIs submit chat observed by the same actual recipient."""
    target = "dual-path-worker"

    async with running_hub() as (_, uri):
        recipient = await connect_agent(target, uri)
        shared = SynapseAgent("dual-edge", uri=uri, verbose=False)
        runtime = SynapseAgentRuntime(shared)
        try:
            assert await asyncio.to_thread(runtime.start)

            def factory(*_args: Any, **_kwargs: Any) -> SynapseAgent:
                """Use the actual runtime-owned agent for the send-only bridge path."""
                return shared

            bridge = SynapseHubBridge(name=shared.name, uri=uri, agent_factory=factory)
            mcp_result = await asyncio.to_thread(
                runtime.run, bridge.send(target, "dual-path payload via MCP")
            )
            assert mcp_result == f"sent to {target}"
            mcp_chat = await recipient.recorder.wait_for(
                lambda frame: frame.get("payload") == "dual-path payload via MCP"
            )
            a2a = A2ABridge(
                agent=shared,
                agent_card={"name": "dual-path-a2a", "capabilities": {}},
                target=target,
                store=A2ATaskStore(),
                submit=runtime.run,
            )
            send_result = await asyncio.to_thread(
                a2a.send_message,
                {
                    "message": {
                        "messageId": "dual-path-a2a-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "dual-path payload via A2A"}],
                        "metadata": {"target": target},
                    }
                },
            )
            task = send_result["task"]
            assert task["id"]
            assert task["status"]["state"] == "TASK_STATE_WORKING"
            assert task["metadata"]["synapseTarget"] == target
            a2a_chat = await recipient.recorder.wait_for(
                lambda frame: frame.get("payload") == "dual-path payload via A2A"
            )
            assert mcp_chat["sender"] == a2a_chat["sender"] == shared.name
            assert mcp_chat["target"] == a2a_chat["target"] == target
        finally:
            await asyncio.to_thread(runtime.stop)
            await close_agents(recipient)


def test_mcp_registration_exposes_send_tool_alongside_a2a_surface() -> None:
    """Registration inventory still lists synapse_send while A2A serve is independent."""
    from synapse_channel.mcp.registration import registered_mcp_tool_names

    tools = registered_mcp_tool_names()
    assert "synapse_send" in tools
    # Sanity: dual-path is not a stub — tool name is the real MCP entry.
    assert isinstance(tools, frozenset)
