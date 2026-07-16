# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the outbound MCP client

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import synapse_channel.core.mcp_outbound as outbound_module
from synapse_channel.core.mcp_config_launch import MCP_SDK_POSIX_DEFAULT_ENV
from synapse_channel.core.mcp_outbound import (
    MCP_EXTRA_HINT,
    MCP_SDK_VERSION,
    McpAccessError,
    McpConfigError,
    McpServerSpec,
    McpToolError,
    OutboundMcpClient,
    _require_mcp,
    _verify_mcp_sdk_contract,
    default_session_opener,
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


# --- allowlist -------------------------------------------------------------


def test_tool_allowed_is_deny_by_default() -> None:
    assert tool_allowed(_spec(allowed_tools=frozenset({"echo"})), "echo") is True
    assert tool_allowed(_spec(allowed_tools=frozenset({"echo"})), "danger") is False
    assert tool_allowed(_spec(allowed_tools=frozenset({"*"})), "anything") is True
    assert tool_allowed(_spec(allowed_tools=frozenset()), "echo") is False


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


def test_mcp_sdk_environment_contract_is_exact_and_fails_closed_on_drift() -> None:
    client_stdio, _mcp_root = _require_mcp()
    _verify_mcp_sdk_contract(client_stdio, installed_version=MCP_SDK_VERSION)

    with pytest.raises(McpConfigError, match="unsupported MCP SDK"):
        _verify_mcp_sdk_contract(client_stdio, installed_version="future")
    drifted = SimpleNamespace(
        DEFAULT_INHERITED_ENV_VARS=[*client_stdio.DEFAULT_INHERITED_ENV_VARS, "NEW_SECRET"],
        PROCESS_TERMINATION_TIMEOUT=client_stdio.PROCESS_TERMINATION_TIMEOUT,
    )
    with pytest.raises(McpConfigError, match="unsupported MCP SDK"):
        _verify_mcp_sdk_contract(drifted, installed_version=MCP_SDK_VERSION)
    cleanup_drift = SimpleNamespace(
        DEFAULT_INHERITED_ENV_VARS=client_stdio.DEFAULT_INHERITED_ENV_VARS,
        PROCESS_TERMINATION_TIMEOUT=99.0,
    )
    with pytest.raises(McpConfigError, match="unsupported MCP SDK"):
        _verify_mcp_sdk_contract(cleanup_drift, installed_version=MCP_SDK_VERSION)


async def test_default_opener_passes_the_current_stderr_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "mcp-server"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    observed: dict[str, Any] = {}

    @asynccontextmanager
    async def stdio_client(params: Any, *, errlog: Any) -> AsyncIterator[tuple[object, object]]:
        observed.update(params=params, errlog=errlog)
        yield object(), object()

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

    client_stdio = SimpleNamespace(
        DEFAULT_INHERITED_ENV_VARS=MCP_SDK_POSIX_DEFAULT_ENV,
        PROCESS_TERMINATION_TIMEOUT=outbound_module.MCP_SDK_TERMINATION_TIMEOUT,
        StdioServerParameters=lambda **kwargs: SimpleNamespace(**kwargs),
        stdio_client=stdio_client,
    )
    mcp_root = SimpleNamespace(ClientSession=lambda *_args: Session())
    monkeypatch.setattr(outbound_module, "_require_mcp", lambda: (client_stdio, mcp_root))

    async with default_session_opener(
        McpServerSpec(name="echo", command=str(executable))
    ) as session:
        assert isinstance(session, Session)

    assert observed["errlog"] is sys.stderr


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
    mcp_spec = importlib.util.find_spec("mcp")
    assert mcp_spec is not None and mcp_spec.origin is not None
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER, encoding="utf-8")
    spec = McpServerSpec(
        name="echo",
        command=str(Path(sys.executable).resolve()),
        args=(str(script),),
        env={"PYTHONPATH": str(Path(mcp_spec.origin).parent.parent)},
        allowed_tools=frozenset({"echo"}),
    )
    client = OutboundMcpClient({"echo": spec}, session_opener=default_session_opener)

    tools = await client.list_tools("echo")
    assert any(tool["name"] == "echo" for tool in tools)
    output = await client.call_tool("echo", "echo", {"text": "hi"})
    assert "echo: hi" in output


async def test_tool_discovery_timeout_is_enforced() -> None:
    class HangingSession(FakeSession):
        async def list_tools(self) -> Any:
            await asyncio.Event().wait()

    spec = McpServerSpec(name="hung", command="/bin/false", timeout_seconds=0.01)
    client = OutboundMcpClient({"hung": spec}, session_opener=_opener(HangingSession(tools=[])))

    with pytest.raises(McpToolError, match="tool discovery.*timed out after 0.01 seconds"):
        await client.list_tools("hung")


async def test_tool_call_timeout_is_enforced() -> None:
    class HangingSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            await asyncio.Event().wait()

    spec = McpServerSpec(
        name="hung",
        command="/bin/false",
        allowed_tools=frozenset({"echo"}),
        timeout_seconds=0.01,
    )
    client = OutboundMcpClient({"hung": spec}, session_opener=_opener(HangingSession(tools=[])))

    with pytest.raises(McpToolError, match="tool 'echo'.*timed out after 0.01 seconds"):
        await client.call_tool("hung", "echo")
