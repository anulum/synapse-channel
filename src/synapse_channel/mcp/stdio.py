# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP stdio lifecycle
"""Stdio lifecycle for the Synapse MCP adapter."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.mcp.bridge import (
    DEFAULT_BRIDGE_NAME,
    DEFAULT_REQUEST_TIMEOUT,
    AgentFactory,
    SynapseHubBridge,
)
from synapse_channel.mcp.registration import build_mcp_server


async def serve_stdio(
    *,
    uri: str = DEFAULT_HUB_URI,
    name: str = DEFAULT_BRIDGE_NAME,
    token: str | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ready_timeout: float = 5.0,
    roles: Iterable[str] = (),
    inbox_feed: str | Path | None = None,
    inbox_cursor: str | Path | None = None,
    agent_factory: AgentFactory = SynapseAgent,
    server_builder: Callable[[SynapseHubBridge], Any] = build_mcp_server,
) -> int:
    """Connect to the hub and run the MCP server over stdio until the client closes.

    Parameters
    ----------
    uri : str, optional
        Hub WebSocket URI.
    name : str, optional
        Identity to register on the hub.
    token : str or None, optional
        Shared-secret token for a secured hub.
    request_timeout : float, optional
        Seconds to await a hub reply before reporting no response. Defaults to
        :data:`DEFAULT_REQUEST_TIMEOUT`.
    ready_timeout : float, optional
        Seconds to wait for the bridge agent handshake before reporting the hub
        unreachable. Defaults to ``5.0``.
    roles : Iterable[str], optional
        Full role identities the bridge answers to.
    inbox_feed, inbox_cursor : str, pathlib.Path, or None, optional
        Local durable relay feed and byte cursor overrides for ``synapse_inbox``.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    server_builder : Callable, optional
        Builds the MCP server from the bridge; injectable for testing.

    Returns
    -------
    int
        ``0`` once the MCP client disconnects, ``1`` when the hub is unreachable.
    """
    bridge = SynapseHubBridge(
        uri=uri,
        name=name,
        token=token,
        request_timeout=request_timeout,
        agent_factory=agent_factory,
        roles=roles,
        inbox_feed=inbox_feed,
        inbox_cursor=inbox_cursor,
    )
    conn_task = asyncio.create_task(bridge.agent.connect())
    try:
        if not await bridge.agent.wait_until_ready(timeout=ready_timeout):
            print(f"[{name}] could not reach hub at {uri}", file=sys.stderr)
            return 1
        server = server_builder(bridge)
        await server.run_stdio_async()
        return 0
    finally:
        bridge.agent.running = False
        conn_task.cancel()
