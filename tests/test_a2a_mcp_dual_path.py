# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP + A2A dual-path against one hub client fixture
"""Prove one representative hub interaction is reachable via MCP and A2A."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.mcp.bridge import SynapseHubBridge


class SharedRecordingAgent(RecordingAgent):
    """Recording agent that also satisfies the MCP bridge async chat surface."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


def test_mcp_send_and_a2a_message_reach_same_target() -> None:
    """MCP ``synapse_send`` and A2A ``message:send`` both deliver to the same target."""
    shared = SharedRecordingAgent()
    target = "dual-path-worker"

    # MCP path: real SynapseHubBridge.send entry (production MCP tool implementation).
    # SharedRecordingAgent implements the chat surface used by send(); cast satisfies
    # the AgentFactory signature without opening a real hub socket.
    def _factory(*_args: Any, **_kwargs: Any) -> SynapseAgent:
        return cast(SynapseAgent, shared)

    mcp_bridge = SynapseHubBridge(
        name="mcp-dual-path",
        agent_factory=_factory,
    )

    async def _mcp_send() -> str:
        return await mcp_bridge.send(target, "dual-path payload via MCP")

    mcp_result = asyncio.run(_mcp_send())
    assert mcp_result == f"sent to {target}"

    # A2A path: real A2ABridge.send_message against the same agent instance.
    a2a = A2ABridge(
        agent=shared,
        agent_card={"name": "dual-path-a2a", "capabilities": {}},
        target=target,
        store=A2ATaskStore(),
    )
    send_result = a2a.send_message(
        {
            "message": {
                "messageId": "dual-path-a2a-1",
                "role": "ROLE_USER",
                "parts": [{"text": "dual-path payload via A2A"}],
                "metadata": {"target": target},
            }
        }
    )
    task = send_result["task"]
    assert task["id"]
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert task["metadata"]["synapseTarget"] == target

    targets = [t for t, _text in shared.messages]
    texts = [text for _t, text in shared.messages]
    assert targets.count(target) >= 2
    assert "dual-path payload via MCP" in texts
    assert "dual-path payload via A2A" in texts

    # Primary observable: both paths addressed the same hub identity.
    assert all(t == target for t in targets)


def test_mcp_registration_exposes_send_tool_alongside_a2a_surface() -> None:
    """Registration inventory still lists synapse_send while A2A serve is independent."""
    from synapse_channel.mcp.registration import registered_mcp_tool_names

    tools = registered_mcp_tool_names()
    assert "synapse_send" in tools
    # Sanity: dual-path is not a stub — tool name is the real MCP entry.
    assert isinstance(tools, frozenset)
