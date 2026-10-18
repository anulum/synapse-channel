# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real MCP reconnect acceptance helpers
"""Observe real public MCP status and join only owned fixture hubs."""

from __future__ import annotations

import asyncio
import contextlib
import json

from mcp import ClientSession
from mcp.types import TextContent

from synapse_channel.core.hub import SynapseHub


async def stop_hub(task: asyncio.Task[None]) -> None:
    """Close only the fixture server and join its socket handlers."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def wait_for_identity(
    session: ClientSession,
    hub: SynapseHub,
    identity: str,
    *,
    also: tuple[str, ...] = (),
) -> float:
    """Require real status snapshots and registration within one finite deadline."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    while loop.time() - started < 10:
        result = await session.call_tool("synapse_status", {})
        if not result.isError and len(result.content) == 1:
            block = result.content[0]
            if isinstance(block, TextContent):
                try:
                    status = json.loads(block.text)
                except json.JSONDecodeError:
                    status = None
                if (
                    isinstance(status, dict)
                    and status.get("identity") == identity
                    and {identity, *also} <= set(hub.online_agents())
                ):
                    return loop.time() - started
        await asyncio.sleep(0.05)
    raise AssertionError("MCP backend did not rejoin the restarted private hub")
