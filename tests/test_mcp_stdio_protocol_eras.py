# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP protocol revisions the stdio face serves
"""Which MCP protocol revisions ``synapse mcp`` serves over stdio.

The HTTPS profile refuses MCP SDK 2's stateless ``2026-07-28`` revision at its
router. The stdio face has no router: it is a local process of the same user,
with no authentication boundary, so the SDK serves both eras there. A client
that opens with ``initialize`` negotiates a handshake revision; a client that
opens with ``server/discover`` gets the stateless revision and the same tools.
These tests pin both answers, so an SDK upgrade that changes either fails here
instead of silently changing what ``docs/mcp.md`` states.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from mcp.types.version import LATEST_HANDSHAKE_VERSION, MODERN_PROTOCOL_VERSIONS

from cli_e2e_helpers import isolated_hub, run_cli
from synapse_channel import __version__
from synapse_channel.mcp.registration import registered_mcp_tool_names

STATELESS = "2026-07-28"


def _bridge(hub_uri: str) -> StdioServerParameters:
    """Launch the packaged ``synapse mcp`` command against ``hub_uri`` over stdio."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "synapse_channel.cli", "mcp", "--uri", hub_uri, "--name", "ERABRIDGE"],
        env=dict(os.environ),
    )


async def test_initialize_negotiates_the_latest_handshake_revision(tmp_path: Path) -> None:
    """A host that sends ``initialize`` gets ``2025-11-25`` and this package's identity."""
    with isolated_hub(tmp_path) as hub:
        async with stdio_client(_bridge(hub.uri)) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert LATEST_HANDSHAKE_VERSION == "2025-11-25"
                assert initialized.protocol_version == LATEST_HANDSHAKE_VERSION
                assert session.discover_result is None
                info = initialized.server_info
                assert (info.name, info.version) == ("synapse", __version__)


async def test_server_discover_opens_the_stateless_revision_with_the_same_tools(
    tmp_path: Path,
) -> None:
    """A host that opens with ``server/discover`` gets ``2026-07-28`` and every tool."""
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", "ERA-1", "--title", "stdio era", uri=hub.uri)
        assert declared.ok(), declared.output
        async with stdio_client(_bridge(hub.uri)) as (read, write):
            async with ClientSession(read, write) as session:
                discovered = await session.discover()
                assert MODERN_PROTOCOL_VERSIONS == (STATELESS,)
                assert discovered.supported_versions == [STATELESS]
                assert session.protocol_version == STATELESS
                assert session.initialize_result is None
                assert discovered.capabilities.tools is not None
                info = session.server_info
                assert info is not None
                assert (info.name, info.version) == ("synapse", __version__)

                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == registered_mcp_tool_names()
                board = await session.call_tool("synapse_board", {})
                assert not board.is_error, board
                block = board.content[0]
                assert isinstance(block, TextContent)
                assert "ERA-1" in json.dumps(json.loads(block.text))
