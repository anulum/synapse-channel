# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live helpers for the Model Context Protocol bridge tests

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent
from synapse_channel.mcp.server import SynapseHubBridge


@dataclass
class BridgeHandle:
    bridge: SynapseHubBridge
    task: asyncio.Task[None]

    async def close(self) -> None:
        self.bridge.agent.running = False
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


async def start_bridge(uri: str, *, name: str = "me", request_timeout: float = 0.5) -> BridgeHandle:
    bridge = SynapseHubBridge(uri=uri, name=name, request_timeout=request_timeout)
    task = asyncio.create_task(bridge.agent.connect())
    handle = BridgeHandle(bridge=bridge, task=task)
    if not await bridge.agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"MCP bridge {name} did not receive hub welcome")
    return handle


async def seed_claim(
    uri: str,
    owner: str,
    task_id: str,
    *,
    paths: list[str] | None = None,
) -> None:
    handle = await connect_agent(owner, uri)
    try:
        await handle.agent.claim(task_id, paths=list(paths or []))
        await handle.recorder.wait_for(
            lambda message: (
                message.get("type") == "claim_granted" and message.get("task_id") == task_id
            )
        )
    finally:
        await close_agents(handle)


async def seed_task(uri: str, task_id: str, title: str) -> None:
    handle = await connect_agent("SEED", uri)
    try:
        await handle.agent.post_task(task_id, title)
        await handle.recorder.wait_for(
            lambda message: (
                message.get("type") == "ledger_task_posted"
                and message.get("task", {}).get("task_id") == task_id
            )
        )
    finally:
        await close_agents(handle)


async def start_manifest_agent(uri: str) -> AgentHandle:
    handle = await connect_agent("FAST", uri)
    await handle.agent.advertise(task_classes=["chat"], skills=["fast-path"])
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "capability_advertised" and message.get("agent") == "FAST"
        )
    )
    return handle
