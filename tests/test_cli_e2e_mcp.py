# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journey for the ``synapse mcp`` stdio bridge.

Unlike the in-process bridge tests, this drives the packaged ``synapse mcp``
command exactly as a Model Context Protocol host (an editor, a desktop client)
would: the MCP SDK's own stdio client launches it as a subprocess, speaks the
real protocol over its stdin/stdout, initialises the session, lists the tools it
projects, and calls them against a live isolated hub. It proves the bridge is a
conformant MCP server end to end, and that a tool call both reads and mutates the
hub's durable state.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import isolated_hub, run_cli

mcp = pytest.importorskip("mcp", reason="the Model Context Protocol SDK is not installed")
mcp_stdio = pytest.importorskip("mcp.client.stdio")
mcp_types = pytest.importorskip("mcp.types")


def _bridge_params(hub_uri: str) -> Any:
    """Launch parameters for ``synapse mcp`` bound to ``hub_uri`` over stdio."""
    return mcp.StdioServerParameters(
        command=sys.executable,
        args=["-m", "synapse_channel.cli", "mcp", "--uri", hub_uri, "--name", "MCPBRIDGE"],
        env=dict(os.environ),
    )


def _text_payload(result: Any) -> str:
    """Return the text of a tool result's first content block, asserting it is text."""
    block = result.content[0]
    assert isinstance(block, mcp_types.TextContent), block
    return str(block.text)


async def test_mcp_bridge_lists_and_reads_hub_tools_over_stdio(tmp_path: Path) -> None:
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", "MCP-1", "--title", "wire the bridge", uri=hub.uri)
        assert declared.ok(), declared.output

        async with mcp_stdio.stdio_client(_bridge_params(hub.uri)) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()

                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {"synapse_board", "synapse_claim", "synapse_task_declare"} <= names

                result = await session.call_tool("synapse_board", {})
                assert not result.isError, result
                board = json.loads(_text_payload(result))
                assert "MCP-1" in json.dumps(board)


async def test_mcp_tool_call_declares_a_task_on_the_hub(tmp_path: Path) -> None:
    with isolated_hub(tmp_path) as hub:
        async with mcp_stdio.stdio_client(_bridge_params(hub.uri)) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "synapse_task_declare",
                    {"task_id": "MCP-2", "title": "declared through the MCP bridge"},
                )
                assert not result.isError, result

        # The task the MCP tool declared is durably on the hub: a plain CLI board
        # read against the same hub — no MCP — now shows it.
        board = run_cli("board", uri=hub.uri)
        assert board.ok(), board.output
        assert "MCP-2" in board.output
