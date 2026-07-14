# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub MCP claim action tests
"""Exercise the responsibility-split claim/release actions on a live hub."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.claim_actions import McpClaimActions


async def test_claim_actions_hold_scope_and_validate_release_receipt() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="mcp-claim-seat")
        try:
            assert isinstance(handle.bridge.claim_actions, McpClaimActions)
            claimed = await handle.bridge.claim("MCP-CLAIM-ACTIONS", ["src/owned.py"])
            recorded = hub.state.claims["MCP-CLAIM-ACTIONS"]
            released = await handle.bridge.release(
                "MCP-CLAIM-ACTIONS",
                evidence=["real-hub claim action test"],
                changed_files=["src/owned.py"],
                confidence="high",
            )
        finally:
            await handle.close()

    assert claimed == "claim granted: 'MCP-CLAIM-ACTIONS' (src/owned.py)"
    assert recorded.owner == "mcp-claim-seat"
    assert recorded.paths == ("src/owned.py",)
    assert released == "released 'MCP-CLAIM-ACTIONS' with receipt owner 'mcp-claim-seat'"
    assert "MCP-CLAIM-ACTIONS" not in hub.state.claims
    assert "evidence=real-hub claim action test" in hub.blackboard.progress[-1].text


async def test_git_claim_action_refuses_ambiguous_scope_before_hub_mutation() -> None:
    bridge = SynapseHubBridge(name="mcp-claim-seat", request_timeout=0.05)

    assert await bridge.git_claim("ESCAPE", ["../outside"]) == (
        "git claim refused: MCP Git claim paths must be bounded repository-relative "
        "paths without traversal."
    )


@pytest.mark.parametrize(
    ("receipt", "message"),
    [
        (None, "no valid receipt"),
        (
            {"task_id": "RECEIPT", "owner": "other", "released": True},
            "mismatched receipt",
        ),
    ],
)
async def test_release_action_refuses_missing_or_mismatched_receipts(
    receipt: dict[str, Any] | None, message: str
) -> None:
    bridge = SynapseHubBridge(name="mcp-claim-seat", request_timeout=0.5)
    release = asyncio.create_task(bridge.release("RECEIPT"))
    for _ in range(50):
        if bridge._waiters:
            break
        await asyncio.sleep(0)
    await bridge.on_message(
        {
            "type": MessageType.RELEASE_GRANTED,
            "task_id": "RECEIPT",
            "receipt": receipt,
        }
    )

    assert message in await release
