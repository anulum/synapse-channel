# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

from hub_e2e_helpers import _free_port, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.mcp.server import SynapseHubBridge, serve_stdio


class RecordingStdioServer:
    """Minimal stdio server adapter that records that the serve path invoked it."""

    def __init__(self) -> None:
        self.ran = False

    async def run_stdio_async(self) -> None:
        self.ran = True


async def test_serve_stdio_unreachable_hub() -> None:
    rc = await serve_stdio(
        uri=f"ws://127.0.0.1:{_free_port()}",
        ready_timeout=0.1,
        server_builder=lambda _bridge: RecordingStdioServer(),
    )
    assert rc == 1


async def test_serve_stdio_runs_until_client_closes() -> None:
    server = RecordingStdioServer()
    async with running_hub(SynapseHub()) as (_, uri):
        rc = await serve_stdio(uri=uri, server_builder=lambda _bridge: server)

    assert rc == 0
    assert server.ran


async def test_serve_stdio_threads_request_timeout_to_the_bridge() -> None:
    captured: list[SynapseHubBridge] = []

    def builder(bridge: SynapseHubBridge) -> RecordingStdioServer:
        captured.append(bridge)
        return RecordingStdioServer()

    async with running_hub(SynapseHub()) as (_, uri):
        rc = await serve_stdio(
            uri=uri,
            server_builder=builder,
            request_timeout=12.5,
            roles=("PROJ/reviewer",),
            inbox_feed="/tmp/mcp-feed",
            inbox_cursor="/tmp/mcp-cursor",
        )

    assert rc == 0
    assert captured[0].request_timeout == 12.5
    assert captured[0].agent.roles == ("PROJ/reviewer",)
    assert str(captured[0].inbox_reader.feed_path) == "/tmp/mcp-feed"
    assert str(captured[0].inbox_reader.cursor_path) == "/tmp/mcp-cursor"
