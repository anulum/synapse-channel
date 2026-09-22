# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real stdio MCP human app task journey
"""Use the packaged MCP entry point and a live hub for the queue lifecycle."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import isolated_hub
from synapse_channel.core.app_tasks import AppTaskError, default_app_task_store, history
from synapse_channel.core.entitlement_store import append_event, default_entitlement_store
from synapse_channel.mcp import app_task_actions

mcp = pytest.importorskip("mcp")
stdio = pytest.importorskip("mcp.client.stdio")


def _seed_allowance(now: datetime) -> None:
    common = {
        "recorded_at": (now - timedelta(hours=1)).isoformat(),
        "source": "operator:private-account",
        "confidence": "operator",
    }
    for event in (
        {
            **common,
            "event_id": "a",
            "kind": "account",
            "account_id": "a",
            "label": "Secret label",
            "status": "active",
        },
        {
            **common,
            "event_id": "p",
            "kind": "pool",
            "pool_id": "p",
            "account_id": "a",
            "unit": "tasks",
        },
        {
            **common,
            "event_id": "w",
            "kind": "window",
            "window_id": "w",
            "pool_id": "p",
            "starts_at": (now - timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat(),
            "grant": "3",
            "unit": "tasks",
            "price_revision": "unknown",
        },
    ):
        append_event(default_entitlement_store(), event)


def _text(result: Any) -> str:
    content = result.content
    assert content and content[0].type == "text"
    return str(content[0].text)


async def test_real_stdio_mcp_task_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    now = datetime.now(timezone.utc)
    _seed_allowance(now)
    bundle = {
        "task_id": "mcp-human-1",
        "prompt": "Private task prompt",
        "input": {"question": "Private input"},
        "window_id": "w",
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "verifier": {"field": "approved", "equals": True},
    }
    result = {
        "task_id": "mcp-human-1",
        "payload": {"approved": True, "answer": "Untrusted private output"},
        "provenance": "operator:manual-app",
        "usage": {"amount": "1", "measurement": "manual"},
    }
    with isolated_hub(tmp_path) as hub:
        params = mcp.StdioServerParameters(
            command=sys.executable,
            args=["-m", "synapse_channel.cli", "mcp", "--uri", hub.uri, "--name", "TEST/app-task"],
            env=dict(os.environ),
        )
        async with stdio.stdio_client(params) as (read, write):
            async with mcp.ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "synapse_app_task_offer",
                    "synapse_app_task_attach",
                    "synapse_app_task_verify",
                } <= tools
                offered = await session.call_tool("synapse_app_task_offer", {"bundle": bundle})
                assert json.loads(_text(offered))["state"] == "offered"
                denied = await session.call_tool(
                    "synapse_app_task_attach",
                    {"task_id": "mcp-human-1", "result": {**result, "task_id": "wrong"}},
                )
                assert denied.isError
                for action, expected in (("accept", "accepted"), ("start", "running")):
                    reply = await session.call_tool(
                        "synapse_app_task_advance", {"task_id": "mcp-human-1", "action": action}
                    )
                    assert json.loads(_text(reply))["state"] == expected
                attached = await session.call_tool(
                    "synapse_app_task_attach", {"task_id": "mcp-human-1", "result": result}
                )
                assert json.loads(_text(attached))["state"] == "result_attached"
                repeated = await session.call_tool(
                    "synapse_app_task_attach", {"task_id": "mcp-human-1", "result": result}
                )
                assert not repeated.isError
                verified = await session.call_tool(
                    "synapse_app_task_verify", {"task_id": "mcp-human-1"}
                )
                assert json.loads(_text(verified))["state"] == "verified"
                corrected = await session.call_tool(
                    "synapse_app_task_correct_usage",
                    {"task_id": "mcp-human-1", "amount": "2", "reason": "receipt correction"},
                )
                assert json.loads(_text(corrected))["usage"]["amount"] == "2"
                status = _text(
                    await session.call_tool("synapse_app_task_status", {"task_id": "mcp-human-1"})
                )
    assert "Private task prompt" not in status
    assert "Untrusted private output" not in status
    assert "Secret label" not in status
    assert "operator:private-account" not in status
    attach_event = next(
        event
        for event in history(default_app_task_store(), "mcp-human-1")
        if event["action"] == "attach"
    )
    assert attach_event["detail"]["actor"] == "TEST/app-task"


def test_missing_private_allowance_does_not_expose_store_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    now = datetime.now(timezone.utc)
    bundle = {
        "task_id": "missing-ledger",
        "prompt": "Private prompt",
        "input": {},
        "window_id": "missing",
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "verifier": {"field": "approved", "equals": True},
    }
    with pytest.raises(AppTaskError, match="^private allowance unavailable$"):
        app_task_actions.offer(bundle)
