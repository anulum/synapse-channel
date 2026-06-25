# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import sys

import pytest

from mcp_server_helpers import agent_of, make_bridge
from synapse_channel.mcp.server import (
    MCP_EXTRA_HINT,
    _require_fastmcp,
    build_mcp_server,
)


def test_require_fastmcp_returns_class() -> None:
    cls = _require_fastmcp()
    assert cls.__name__ == "FastMCP"


def test_require_fastmcp_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        _require_fastmcp()


async def test_build_registers_tools_and_resources() -> None:
    server = build_mcp_server(make_bridge())
    tool_names = {tool.name for tool in await server.list_tools()}
    assert {
        "synapse_claim",
        "synapse_release",
        "synapse_send",
        "synapse_handoff",
        "synapse_task_declare",
        "synapse_task_update",
        "synapse_board",
        "synapse_state",
        "synapse_manifest",
    } <= tool_names
    resource_uris = {str(resource.uri) for resource in await server.list_resources()}
    assert any("board" in uri for uri in resource_uris)
    assert any("state" in uri for uri in resource_uris)
    assert any("manifest" in uri for uri in resource_uris)


async def test_every_tool_and_resource_wrapper_dispatches() -> None:
    bridge = make_bridge(request_timeout=0.05)
    server = build_mcp_server(bridge)
    await server.call_tool("synapse_claim", {"task_id": "T", "paths": ["a"]})
    await server.call_tool("synapse_release", {"task_id": "T"})
    await server.call_tool("synapse_send", {"target": "X", "message": "m"})
    await server.call_tool("synapse_handoff", {"task_id": "T", "to_agent": "Y"})
    await server.call_tool("synapse_task_declare", {"task_id": "T", "title": "t"})
    await server.call_tool("synapse_task_update", {"task_id": "T", "status": "done"})
    await server.call_tool("synapse_board", {})
    await server.call_tool("synapse_state", {})
    await server.call_tool("synapse_manifest", {})
    await server.read_resource("synapse://board")
    await server.read_resource("synapse://state")
    await server.read_resource("synapse://manifest")
    kinds = {call[0] for call in agent_of(bridge).calls}
    assert {
        "claim",
        "release",
        "chat",
        "handoff",
        "post_task",
        "update_ledger_task",
        "request_board",
        "request_state",
        "request_manifest",
    } <= kinds


async def test_build_requires_mcp_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match=r"\[mcp\]"):
        build_mcp_server(make_bridge())
    assert "synapse-channel[mcp]" in MCP_EXTRA_HINT
