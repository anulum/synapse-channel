# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real localhost WebSocket helpers for client tests

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import _free_port
from synapse_channel.client.agent import SynapseAgent


async def wait_for_recorded_count(
    messages: list[dict[str, Any]],
    count: int,
    *,
    timeout: float = 2.0,
) -> list[dict[str, Any]]:
    """Wait until the recording endpoint has received ``count`` messages."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if len(messages) >= count:
            return messages
        await asyncio.sleep(0.01)
    raise TimeoutError(f"recorded {len(messages)} messages, expected at least {count}")


@asynccontextmanager
async def connected_recording_agent(
    name: str,
    **agent_kwargs: Any,
) -> AsyncIterator[tuple[SynapseAgent, list[dict[str, Any]]]]:
    """Run a real localhost WebSocket endpoint and connect one client to it."""
    messages: list[dict[str, Any]] = []
    ready = asyncio.Event()

    async def handler(websocket: ServerConnection) -> None:
        async for raw in websocket:
            payload = json.loads(raw)
            messages.append(payload)
            if payload.get("type") == "heartbeat" and not ready.is_set():
                await websocket.send(json.dumps({"type": "welcome", "hub_id": "recording-hub"}))
                ready.set()

    port = _free_port()
    server = await serve(handler, "localhost", port)
    agent = SynapseAgent(
        name,
        uri=f"ws://localhost:{port}",
        heartbeat_interval=60.0,
        verbose=False,
        **agent_kwargs,
    )
    task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=2.0):
            raise TimeoutError("recording websocket did not complete client registration")
        yield agent, messages
    finally:
        agent.running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        server.close()
        await server.wait_closed()
