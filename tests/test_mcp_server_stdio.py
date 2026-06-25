# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

from typing import Any, cast

from mcp_server_helpers import FakeAgent
from synapse_channel.mcp.server import (
    AgentFactory,
    SynapseHubBridge,
    serve_stdio,
)


class FakeServer:
    """A FastMCP stand-in whose stdio run records that it was invoked."""

    def __init__(self) -> None:
        self.ran = False

    async def run_stdio_async(self) -> None:
        self.ran = True


async def test_serve_stdio_unreachable_hub() -> None:
    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, **kwargs)
        agent.ready = False
        return agent

    rc = await serve_stdio(
        agent_factory=cast(AgentFactory, factory), server_builder=lambda _b: FakeServer()
    )
    assert rc == 1


async def test_serve_stdio_runs_until_client_closes() -> None:
    server = FakeServer()
    rc = await serve_stdio(
        agent_factory=cast(AgentFactory, FakeAgent), server_builder=lambda _b: server
    )
    assert rc == 0
    assert server.ran


async def test_serve_stdio_threads_request_timeout_to_the_bridge() -> None:
    captured: list[SynapseHubBridge] = []

    def builder(bridge: SynapseHubBridge) -> FakeServer:
        captured.append(bridge)
        return FakeServer()

    rc = await serve_stdio(
        agent_factory=cast(AgentFactory, FakeAgent),
        server_builder=builder,
        request_timeout=12.5,
    )
    assert rc == 0
    assert captured[0].request_timeout == 12.5
