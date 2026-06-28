# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the outbound MCP client

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel.core.mcp_outbound import (
    MCP_EXTRA_HINT,
    McpAccessError,
    McpConfigError,
    McpServerSpec,
    McpToolError,
    OutboundMcpClient,
    _require_mcp,
    default_session_opener,
    load_outbound_config,
    result_text,
    tool_allowed,
)


class FakeSession:
    def __init__(self, *, tools: list[str], error: bool = False) -> None:
        self._tools = tools
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[SimpleNamespace(name=name, description=f"{name} desc") for name in self._tools]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, arguments or {}))
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"ran {name}"), SimpleNamespace(text="line2")],
            isError=self._error,
        )


def _opener(session: FakeSession) -> Any:
    @asynccontextmanager
    async def opener(spec: McpServerSpec) -> AsyncIterator[FakeSession]:
        yield session

    return opener


def _spec(**overrides: Any) -> McpServerSpec:
    base: dict[str, Any] = {"name": "fs", "command": "server", "allowed_tools": frozenset({"echo"})}
    base.update(overrides)
    return McpServerSpec(**base)


# --- config + allowlist ----------------------------------------------------


def test_tool_allowed_is_deny_by_default() -> None:
    assert tool_allowed(_spec(allowed_tools=frozenset({"echo"})), "echo") is True
    assert tool_allowed(_spec(allowed_tools=frozenset({"echo"})), "danger") is False
    assert tool_allowed(_spec(allowed_tools=frozenset({"*"})), "anything") is True
    assert tool_allowed(_spec(allowed_tools=frozenset()), "echo") is False


def test_load_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "fs",
                        "command": "mcp-fs",
                        "args": ["--root", "/data"],
                        "allowed_tools": ["read", "list"],
                        "env": {"K": "V"},
                        "timeout_seconds": 12,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    servers = load_outbound_config(path)
    assert set(servers) == {"fs"}
    spec = servers["fs"]
    assert spec.command == "mcp-fs"
    assert spec.args == ("--root", "/data")
    assert spec.allowed_tools == frozenset({"read", "list"})
    assert spec.env == {"K": "V"}
    assert spec.timeout_seconds == 12


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("{}", "'servers' list"),
        ('{"servers": [1]}', "must be an object"),
        ('{"servers": [{"name": "", "command": "x"}]}', "non-empty name and command"),
        (
            '{"servers": [{"name": "a", "command": "x"}, {"name": "a", "command": "y"}]}',
            "duplicate",
        ),
        ("{not json", "invalid MCP config JSON"),
    ],
)
def test_load_config_rejects_bad_files(tmp_path: Path, content: str, match: str) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(McpConfigError, match=match):
        load_outbound_config(path)


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(McpConfigError, match="does not exist"):
        load_outbound_config(tmp_path / "absent.json")


def test_result_text_joins_text_blocks() -> None:
    result = SimpleNamespace(content=[SimpleNamespace(text="a"), SimpleNamespace(other=1)])
    assert result_text(result) == "a"
    assert result_text(SimpleNamespace(content=None)) == ""


# --- client behaviour ------------------------------------------------------


async def test_list_tools_filters_to_the_allowlist() -> None:
    session = FakeSession(tools=["echo", "danger"])
    client = OutboundMcpClient({"fs": _spec()}, session_opener=_opener(session))
    tools = await client.list_tools("fs")
    assert [tool["name"] for tool in tools] == ["echo"]
    assert tools[0]["description"] == "echo desc"


async def test_call_tool_runs_an_allowlisted_tool() -> None:
    session = FakeSession(tools=["echo"])
    client = OutboundMcpClient({"fs": _spec()}, session_opener=_opener(session))
    output = await client.call_tool("fs", "echo", {"x": 1})
    assert output == "ran echo\nline2"
    assert session.calls == [("echo", {"x": 1})]


async def test_call_tool_denies_a_non_allowlisted_tool() -> None:
    client = OutboundMcpClient(
        {"fs": _spec()}, session_opener=_opener(FakeSession(tools=["danger"]))
    )
    with pytest.raises(McpAccessError, match="not allowed by the config"):
        await client.call_tool("fs", "danger")


async def test_call_tool_denies_an_unknown_server() -> None:
    client = OutboundMcpClient({"fs": _spec()})
    with pytest.raises(McpAccessError, match="not in the outbound MCP allowlist"):
        await client.call_tool("other", "echo")


async def test_call_tool_raises_on_error_result() -> None:
    session = FakeSession(tools=["echo"], error=True)
    client = OutboundMcpClient({"fs": _spec()}, session_opener=_opener(session))
    with pytest.raises(McpToolError, match="returned an error"):
        await client.call_tool("fs", "echo")


def test_server_names_are_sorted() -> None:
    client = OutboundMcpClient({"b": _spec(name="b"), "a": _spec(name="a")})
    assert client.server_names() == ["a", "b"]


# --- optional dependency ---------------------------------------------------


def test_require_mcp_raises_a_clear_hint_when_absent() -> None:
    def _no_mcp(name: str) -> Any:
        raise ImportError(name)

    with pytest.raises(RuntimeError, match="optional extra"):
        _require_mcp(import_module=_no_mcp)
    assert "synapse-channel[mcp]" in MCP_EXTRA_HINT


def test_require_mcp_returns_the_sdk_modules_when_installed() -> None:
    pytest.importorskip("mcp.client.stdio")
    client_stdio, mcp_root = _require_mcp()
    assert hasattr(client_stdio, "StdioServerParameters")
    assert hasattr(mcp_root, "ClientSession")


_ECHO_SERVER = """
from mcp.server.fastmcp import FastMCP

server = FastMCP("echo-test")


@server.tool()
def echo(text: str) -> str:
    return f"echo: {text}"


if __name__ == "__main__":
    server.run()
"""


async def test_call_tool_against_a_real_stdio_mcp_server(tmp_path: Path) -> None:
    pytest.importorskip("mcp.server.fastmcp")
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER, encoding="utf-8")
    spec = McpServerSpec(
        name="echo",
        command=sys.executable,
        args=(str(script),),
        allowed_tools=frozenset({"echo"}),
    )
    client = OutboundMcpClient({"echo": spec}, session_opener=default_session_opener)

    tools = await client.list_tools("echo")
    assert any(tool["name"] == "echo" for tool in tools)
    output = await client.call_tool("echo", "echo", {"text": "hi"})
    assert "echo: hi" in output
