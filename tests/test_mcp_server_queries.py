# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import json

from hub_e2e_helpers import close_agents, running_hub
from mcp_server_helpers import seed_claim, seed_task, start_bridge, start_manifest_agent
from synapse_channel.mcp.server import SynapseHubBridge


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


async def test_manifest_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.manifest()
    assert "did not return the manifest" in out
