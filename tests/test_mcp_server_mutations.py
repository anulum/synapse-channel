# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import asyncio

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from mcp_server_helpers import seed_claim, start_bridge
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.server import SynapseHubBridge


async def test_claim_granted() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="me")
        try:
            out = await handle.bridge.claim("T1", ["src/a.py"])
        finally:
            await handle.close()
        assert "granted" in out
        assert "src/a.py" in out
        assert hub.state.claims["T1"].owner == "me"


async def test_claim_granted_whole_worktree() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="me")
        try:
            out = await handle.bridge.claim("T1")
        finally:
            await handle.close()
        assert "whole worktree" in out
        assert hub.state.claims["T1"].paths == ()


async def test_claim_denied() -> None:
    async with running_hub() as (_, uri):
        await seed_claim(uri, "ALPHA", "T1")
        handle = await start_bridge(uri, name="me")
        try:
            out = await handle.bridge.claim("T1")
        finally:
            await handle.close()
    assert "denied" in out
    assert "ALPHA" in out


async def test_claim_grant_for_other_owner_is_not_mine() -> None:
    bridge = SynapseHubBridge(name="me", request_timeout=0.05)
    task = asyncio.create_task(bridge.claim("T1"))
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "OTHER"})
    out = await task
    assert "no response" in out


async def test_claim_timeout() -> None:
    bridge = SynapseHubBridge(name="me", request_timeout=0.05)
    out = await bridge.claim("T1")
    assert "no response" in out


async def test_claim_ignores_reply_for_another_task() -> None:
    bridge = SynapseHubBridge(name="me", request_timeout=0.5)
    task = asyncio.create_task(bridge.claim("T1"))
    for _ in range(50):
        if bridge._waiters:
            break
        await asyncio.sleep(0)
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "OTHER", "owner": "me"})
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    out = await task
    assert "granted" in out


async def test_release_granted() -> None:
    async with running_hub() as (hub, uri):
        await seed_claim(uri, "me", "T1")
        handle = await start_bridge(uri, name="me")
        try:
            out = await handle.bridge.release("T1")
        finally:
            await handle.close()
        assert "released 'T1'" in out
        assert "T1" not in hub.state.claims


async def test_release_denied() -> None:
    async with running_hub() as (_, uri):
        await seed_claim(uri, "ALPHA", "T1")
        handle = await start_bridge(uri, name="me")
        try:
            out = await handle.bridge.release("T1")
        finally:
            await handle.close()
    assert "denied" in out


async def test_release_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.release("T1")
    assert "no response" in out


async def test_send_dispatches_chat() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.send("ALPHA", "status?")
            chat = await alpha.recorder.wait_for(
                lambda message: (
                    message.get("type") == "chat" and message.get("payload") == "status?"
                )
            )
        finally:
            await handle.close()
            await close_agents(alpha)
    assert out == "sent to ALPHA"
    assert chat["sender"] == "me"


async def test_handoff_granted() -> None:
    async with running_hub() as (hub, uri):
        beta = await connect_agent("BETA", uri)
        handle = await start_bridge(uri, name="me")
        try:
            assert "granted" in await handle.bridge.claim("T1")
            out = await handle.bridge.handoff("T1", "BETA")
        finally:
            await handle.close()
            await close_agents(beta)
        assert "handed off 'T1' to BETA" in out
        assert hub.state.claims["T1"].owner == "BETA"


async def test_handoff_denied() -> None:
    async with running_hub() as (_, uri):
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.handoff("T1", "BETA")
        finally:
            await handle.close()
    assert "denied" in out


async def test_handoff_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.handoff("T1", "BETA")
    assert "no response" in out


async def test_task_declare_posted() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri)
        try:
            out = await handle.bridge.task_declare("T1", "Build", ["T0"])
        finally:
            await handle.close()
        assert "declared 'T1'" in out
        assert "Build" in out
        assert hub.blackboard.tasks["T1"].depends_on == ("T0",)


async def test_task_declare_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.task_declare("T1", "Build")
    assert "no response" in out


async def test_task_update_updated() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri)
        try:
            assert "declared" in await handle.bridge.task_declare("T1", "Build")
            out = await handle.bridge.task_update("T1", "done")
        finally:
            await handle.close()
        assert "status=done" in out
        assert hub.blackboard.tasks["T1"].status == "done"


async def test_task_update_timeout() -> None:
    bridge = SynapseHubBridge(request_timeout=0.05)
    out = await bridge.task_update("T1", "done")
    assert "no response" in out
