# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real stdio MCP onboarding journey
"""Exercise inbox/status discovery and connection loss through the stdio process."""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import isolated_hub
from synapse_channel.relay import append_jsonl, encode_lite

mcp = pytest.importorskip("mcp", reason="the Model Context Protocol SDK is not installed")
mcp_stdio = pytest.importorskip("mcp.client.stdio")
mcp_types = pytest.importorskip("mcp.types")

IDENTITY = "PROJ/client"


def _text_payload(result: Any) -> str:
    """Return the first text content block from a real MCP tool result."""
    block = result.content[0]
    assert isinstance(block, mcp_types.TextContent), block
    return str(block.text)


async def test_stdio_host_discovers_and_calls_inbox_and_status(tmp_path: Path) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "inbox.cursor"
    append_jsonl(
        feed,
        encode_lite(
            {
                "type": "chat",
                "sender": "PEER",
                "target": IDENTITY,
                "payload": "durable MCP body",
                "timestamp": 1.0,
                "msg_id": 9,
            }
        ),
    )
    with isolated_hub(tmp_path) as hub:
        params = mcp.StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "synapse_channel.cli",
                "mcp",
                "--uri",
                hub.uri,
                "--name",
                IDENTITY,
                "--inbox-feed",
                str(feed),
                "--inbox-cursor",
                str(cursor),
            ],
            env=dict(os.environ),
        )
        async with mcp_stdio.stdio_client(params) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                inbox_result = await session.call_tool("synapse_inbox", {"limit": 10})
                status_result = await session.call_tool("synapse_status", {})
                empty_result = await session.call_tool("synapse_inbox", {})

    inbox = json.loads(_text_payload(inbox_result))
    status = json.loads(_text_payload(status_result))
    empty = json.loads(_text_payload(empty_result))
    assert {"synapse_inbox", "synapse_status", "synapse_claim", "synapse_board"} <= tools
    assert [message["payload"] for message in inbox["messages"]] == ["durable MCP body"]
    assert status["identity"] == IDENTITY
    assert status["mailbox_pending"] == 0
    assert status["mailbox_pending_available"] is True
    assert empty["messages"] == []
    assert cursor.stat().st_size > 0


async def test_stdio_send_reports_connection_loss_as_an_error(tmp_path: Path) -> None:
    """An actual stdio host cannot report a send after its hub has terminated."""
    with ExitStack() as fixtures:
        hub = fixtures.enter_context(isolated_hub(tmp_path))
        params = mcp.StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "synapse_channel.cli",
                "mcp",
                "--uri",
                hub.uri,
                "--name",
                IDENTITY,
                "--request-timeout",
                "0.25",
            ],
            env=dict(os.environ),
        )
        async with mcp_stdio.stdio_client(params) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()
                sent = await session.call_tool(
                    "synapse_send", {"target": "all", "message": "connected fixture"}
                )
                assert sent.isError is False
                assert _text_payload(sent) == "sent to all"
                fixtures.close()
                unavailable = await session.call_tool("synapse_board", {})
                assert _text_payload(unavailable) == "the hub did not return the board"
                refused = await session.call_tool(
                    "synapse_send", {"target": "all", "message": "disconnected fixture"}
                )
                assert refused.isError is True
                assert "hub connection unavailable" in _text_payload(refused)
