# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — registered MCP storage refusal and recovery journeys
"""Exercise storage failures through the real SDK, CLI bridge and live hub."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from cli_e2e_helpers import isolated_hub, run_cli
from synapse_channel.core.persistence import EventStore
from synapse_channel.mailbox_cursor import load_cursor
from synapse_channel.relay import append_jsonl, encode_lite

pytestmark = pytest.mark.real_hub

_IDENTITY = "MCP-STORAGE/bridge"
_TASK = "MCP-STORAGE-TASK"


@asynccontextmanager
async def _session(uri: str, root: Path, feed: Path, cursor: Path) -> AsyncIterator[ClientSession]:
    """Launch the candidate CLI and initialise its registered stdio tools."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    env["COLUMNS"] = "200"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "synapse_channel.cli",
            "mcp",
            "--uri",
            uri,
            "--name",
            _IDENTITY,
            "--inbox-feed",
            str(feed),
            "--inbox-cursor",
            str(cursor),
        ],
        env=env,
    )
    with (root / "server.stderr").open("a", encoding="utf-8") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"synapse_inbox", "synapse_route_task", "synapse_memory_recall"} <= names
                yield session


def _text(result: CallToolResult) -> str:
    """Read the actual SDK tool result without discarding protocol errors."""
    assert not result.isError, result
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _append(feed: Path) -> None:
    """Write one real retained relay envelope for the bridge identity."""
    append_jsonl(
        feed,
        encode_lite(
            {
                "type": "chat",
                "sender": "MCP-STORAGE/peer",
                "target": _IDENTITY,
                "payload": "replay-safe",
                "timestamp": 1.0,
                "msg_id": 7,
            }
        ),
    )


@pytest.mark.parametrize("fault", ["feed-directory", "cursor-directory", "cursor-parent-file"])
async def test_registered_inbox_refuses_storage_faults_and_recovers(
    tmp_path: Path, fault: str
) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "PRIVATE_CURSOR_CANARY" / "cursor"
    if fault == "feed-directory":
        feed.mkdir()
        expected = "cannot read local relay feed"
    else:
        _append(feed)
        if fault == "cursor-directory":
            cursor.mkdir(parents=True)
        else:
            cursor.parent.write_text("blocked parent", encoding="utf-8")
        expected = "cannot persist MCP inbox cursor; messages may repeat"
    with isolated_hub(tmp_path) as hub:
        async with _session(hub.uri, tmp_path, feed, cursor) as session:
            first = json.loads(_text(await session.call_tool("synapse_inbox", {})))
            second = json.loads(_text(await session.call_tool("synapse_inbox", {})))
            assert first["error"] == second["error"] == expected
            assert first["available"] is False
            assert first["cursor"] == second["cursor"] == 0
            assert first["source"] == str(feed)
            assert "PRIVATE_CURSOR_CANARY" not in first["error"]
            assert "Errno" not in first["error"]
            assert first["messages"] == second["messages"]
            assert first["has_more"] is (fault != "feed-directory")
            assert load_cursor(cursor) == 0
            _text(await session.call_tool("synapse_board", {}))
            if fault == "feed-directory":
                feed.rmdir()
                _append(feed)
            elif fault == "cursor-directory":
                cursor.rmdir()
            else:
                cursor.parent.unlink()
            recovered = json.loads(_text(await session.call_tool("synapse_inbox", {})))
            assert recovered["available"] is True
            assert "error" not in recovered
            assert [message["payload"] for message in recovered["messages"]] == ["replay-safe"]
            assert recovered["cursor"] == load_cursor(cursor) == feed.stat().st_size
            assert recovered["has_more"] is False
        async with _session(hub.uri, tmp_path, feed, cursor) as reopened:
            settled = json.loads(_text(await reopened.call_tool("synapse_inbox", {})))
            assert settled["available"] is True
            assert settled["messages"] == []
            assert settled["cursor"] == recovered["cursor"]
    assert expected in (tmp_path / "server.stderr").read_text(encoding="utf-8")


@pytest.mark.parametrize("tool", ["synapse_memory_recall", "synapse_route_task"])
@pytest.mark.parametrize("fault", ["store-directory", "corrupt-database", "invalid-key"])
async def test_registered_advisory_storage_errors_remain_safe_and_retryable(
    tmp_path: Path,
    tool: str,
    fault: str,
) -> None:
    feed = tmp_path / "feed.ndjson"
    cursor = tmp_path / "cursor"
    db = tmp_path / "PRIVATE_STORE_CANARY.db"
    key = tmp_path / "PRIVATE_KEY_CANARY"
    if fault == "store-directory":
        db.mkdir()
    elif fault == "corrupt-database":
        db.write_bytes(b"not a SQLite database")
    else:
        EventStore(db).close()
        key.write_bytes(b"not a key")
        key.chmod(0o600)
    args: dict[str, Any] = {"event_store": str(db)}
    if tool == "synapse_memory_recall":
        args["query"] = "recovery"
        expected = "cannot read memory recall event store"
    else:
        args["task_id"] = _TASK
        expected = "cannot read observed capability event store"
    if fault == "invalid-key":
        args["event_store_key_file"] = str(key)
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", _TASK, "--title", "recovery", uri=hub.uri)
        assert declared.ok(), declared.output
        async with _session(hub.uri, tmp_path, feed, cursor) as session:
            for _ in range(2):
                result = _text(await session.call_tool(tool, args))
                assert result == expected
                assert "PRIVATE_" not in result
            _text(await session.call_tool("synapse_board", {}))
            if fault == "store-directory":
                db.rmdir()
            elif fault == "corrupt-database":
                db.unlink()
            else:
                del args["event_store_key_file"]
            store = EventStore(db)
            store.close()
            recovered = json.loads(_text(await session.call_tool(tool, args)))
            assert "trust_boundary" in recovered
        async with _session(hub.uri, tmp_path, feed, cursor) as reopened:
            assert json.loads(_text(await reopened.call_tool(tool, args))) == recovered
    assert expected in (tmp_path / "server.stderr").read_text(encoding="utf-8")


@pytest.mark.parametrize("tool", ["synapse_memory_recall", "synapse_route_task"])
async def test_registered_advisory_preserves_authored_missing_store_refusal(
    tmp_path: Path,
    tool: str,
) -> None:
    db = tmp_path / "missing.db"
    args: dict[str, Any] = {"event_store": str(db)}
    if tool == "synapse_memory_recall":
        args["query"] = "recovery"
    else:
        args["task_id"] = _TASK
    with isolated_hub(tmp_path) as hub:
        declared = run_cli("task", "declare", _TASK, "--title", "recovery", uri=hub.uri)
        assert declared.ok(), declared.output
        async with _session(hub.uri, tmp_path, tmp_path / "feed", tmp_path / "cursor") as session:
            assert _text(await session.call_tool(tool, args)) == f"missing event store: {db}"
            assert db.exists() is False


@pytest.mark.parametrize("tool", ["synapse_inbox", "synapse_memory_recall"])
async def test_registered_storage_tools_validate_malformed_arguments(
    tmp_path: Path, tool: str
) -> None:
    with isolated_hub(tmp_path) as hub:
        async with _session(hub.uri, tmp_path, tmp_path / "feed", tmp_path / "cursor") as session:
            args: dict[str, Any] = {"limit": {"unexpected": 7}}
            if tool == "synapse_memory_recall":
                args.update(event_store=str(tmp_path / "missing.db"), query="recovery")
            result = await session.call_tool(tool, args)
            assert result.isError
            assert any(
                isinstance(block, TextContent) and "validation error" in block.text
                for block in result.content
            )
            _text(await session.call_tool("synapse_board", {}))
