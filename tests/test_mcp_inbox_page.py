# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — caller-cursor inbox native transport contracts
"""Exercise repeatable pages against the real native hub and MCP catalogue."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from mcp_server_helpers import BridgeHandle, start_bridge
from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.hub_inbox_cursor import hub_inbox_cursor_path
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.registration import build_mcp_server
from synapse_channel.relay import append_jsonl, encode_lite
from test_hub_inbox import fence


def _structured_text(result: object) -> str:
    """Return the text a tool answered with, refusing a protocol error."""
    assert isinstance(result, CallToolResult), result
    assert not result.is_error, result
    assert result.structured_content is not None
    text = result.structured_content["result"]
    assert isinstance(text, str)
    return text


async def test_pages_are_repeatable_exact_and_independent_of_local_cursors(tmp_path: Path) -> None:
    """Lost replies replay; group traffic and a local feed never become directed pages."""
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_, uri):
            publisher = await connect_agent("P/publisher", uri)
            feed, cursor = tmp_path / "local.ndjson", tmp_path / "local.cursor"
            append_jsonl(
                feed,
                encode_lite(
                    {
                        "type": "chat",
                        "sender": "LOCAL",
                        "target": "P/reader",
                        "payload": "local only",
                    }
                ),
            )
            bridge = SynapseHubBridge(
                uri=uri, name="P/reader", request_timeout=2, inbox_feed=feed, inbox_cursor=cursor
            )
            handle = BridgeHandle(bridge, asyncio.create_task(bridge.agent.connect()))
            server = build_mcp_server(handle.bridge)
            try:
                assert await bridge.agent.wait_until_ready(2)
                for target, text in [
                    ("all", "broadcast"),
                    ("P/*", "group"),
                    ("P/reader,P/foreign", "multi"),
                    ("P/foreign", "foreign"),
                    ("P/reader", "first"),
                    ("P/reader", "second"),
                ]:
                    await publisher.agent.chat(text, target=target)
                await fence(publisher)
                start = json.loads(await handle.bridge.inbox_page(limit=1))
                assert start["messages"] == [] and start["has_more"] is True
                page = json.loads(await handle.bridge.inbox_page())
                assert [row["payload"] for row in page["messages"]] == ["first", "second"]
                assert json.loads(await handle.bridge.inbox_page()) == page
                result = await server.call_tool("synapse_inbox_page", {})
                assert json.loads(_structured_text(result)) == page
                advanced = json.loads(
                    await handle.bridge.inbox_page(hub_id=page["hub_id"], after_seq=page["cursor"])
                )
                assert advanced["messages"] == [] and advanced["has_more"] is False
                assert not hub_inbox_cursor_path(handle.bridge.inbox_home, uri, "P/reader").exists()
                assert not cursor.exists()
                assert "source" in page and page["source"] == "hub"
                legacy = await server.call_tool("synapse_inbox", {})
                assert [
                    row["payload"] for row in json.loads(_structured_text(legacy))["messages"]
                ] == ["local only"]
                assert cursor.exists()
                assert json.loads(await bridge.inbox_page())["messages"] == page["messages"]
                schema = next(
                    t for t in await server.list_tools() if t.name == "synapse_inbox_page"
                )
                for name in ("hub_id", "after_seq", "limit"):
                    assert schema.input_schema["properties"][name]["description"]
            finally:
                await handle.close()
                await close_agents(publisher)
    finally:
        store.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"hub_id": None},
        {"hub_id": "x" * 257},
        {"after_seq": True},
        {"after_seq": -1},
        {"after_seq": 2**63},
        {"after_seq": 1},
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
    ],
)
async def test_invalid_cursors_refuse_before_connecting(arguments: dict[str, Any]) -> None:
    """The public bridge rejects malformed bounds without opening or reading any source."""
    bridge = SynapseHubBridge(uri="ws://127.0.0.1:1", name="P/reader", request_timeout=0.05)
    with pytest.raises(ValueError, match="invalid durable inbox page"):
        await bridge.inbox_page(**arguments)
    assert bridge.agent.connection is None


@pytest.mark.parametrize("profile", ["no-journal", "acl-denied", "wrong-hub", "beyond-journal"])
async def test_unavailable_native_profiles_refuse_without_consumption(
    tmp_path: Path, profile: str
) -> None:
    """Native refusals and missing permission never report a successful empty inbox."""
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        journal=None if profile == "no-journal" else store,
        acl_policy=AclPolicy() if profile == "acl-denied" else None,
        require_acl=profile == "acl-denied",
    )
    try:
        async with running_hub(hub) as (_, uri):
            handle = await start_bridge(uri, name="P/reader", request_timeout=0.1)
            try:
                arguments: dict[str, Any] = {}
                if profile == "wrong-hub":
                    arguments["hub_id"] = "different-hub"
                if profile == "beyond-journal":
                    arguments = {"hub_id": handle.bridge.agent.hub_id, "after_seq": 2**62}
                with pytest.raises(RuntimeError, match="caller cursor unchanged"):
                    await handle.bridge.inbox_page(**arguments)
                assert not hub_inbox_cursor_path(handle.bridge.inbox_home, uri, "P/reader").exists()
            finally:
                await handle.close()
    finally:
        store.close()


@pytest.mark.parametrize("case", ["connection-binding", "page-limit", "correlation"])
async def test_native_wire_pages_preserve_binding_limit_and_correlation(case: str) -> None:
    """The public bridge validates actual wire replies before publishing page content."""
    from websockets.asyncio.server import ServerConnection, serve

    async def endpoint(socket: ServerConnection) -> None:
        await socket.send(json.dumps({"type": "welcome", "hub_id": "syn-page-wire"}))
        async for raw in socket:
            request = json.loads(raw)
            if request.get("type") != "history_request":
                continue
            page = {
                "version": 1,
                "available": True,
                "identity": "P/reader",
                "hub_id": "other-hub" if case == "connection-binding" else "syn-page-wire",
                "cursor": 2,
                "has_more": False,
                "messages": [
                    {
                        "type": "chat",
                        "seq": seq,
                        "sender": "P/publisher",
                        "target": "P/reader",
                        "payload": "wire message",
                    }
                    for seq in range(1, 3 if case == "page-limit" else 2)
                ],
            }
            if case == "correlation":
                await socket.send(
                    json.dumps({"type": "who_snapshot", "request_id": request["request_id"]})
                )
                await socket.send(
                    json.dumps(
                        {"type": "history_snapshot", "request_id": "unrelated", "inbox_page": page}
                    )
                )
            await socket.send(
                json.dumps(
                    {
                        "type": "history_snapshot",
                        "request_id": request["request_id"],
                        "inbox_page": page,
                    }
                )
            )

    async with serve(endpoint, "127.0.0.1", 0) as socket_server:
        port = socket_server.sockets[0].getsockname()[1]
        handle = await start_bridge(f"ws://127.0.0.1:{port}", name="P/reader", request_timeout=1)
        try:
            if case == "correlation":
                page = json.loads(await handle.bridge.inbox_page(limit=1))
                assert [row["payload"] for row in page["messages"]] == ["wire message"]
            else:
                with pytest.raises(RuntimeError, match="caller cursor unchanged"):
                    await handle.bridge.inbox_page(limit=1)
        finally:
            await handle.close()
