# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound MCP client with a deny-by-default tool allowlist
"""Call external MCP tools from Synapse with explicit trust boundaries.

This is the outbound direction of the MCP face: where ``synapse mcp`` exposes the
hub *to* MCP clients, this client lets a Synapse worker *call* tools on an
external MCP server. Trust is deny-by-default and config-bound — an agent may
only reach a server named in the config, and only the tools that server's entry
allowlists (``"*"`` opts the whole server in). A tool that is not allowlisted is
refused before the server is ever contacted.

The configuration parsing and the allowlist are pure and unit-testable; the
actual MCP session is opened through an injectable opener so the orchestration is
testable without a live subprocess. The ``mcp`` SDK is an optional extra
(``pip install 'synapse-channel[mcp]'``); importing this module never requires it.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

MCP_EXTRA_HINT = "outbound MCP calls need the optional extra: pip install 'synapse-channel[mcp]'"
WILDCARD = "*"


class McpConfigError(ValueError):
    """Raised when the outbound MCP config file is malformed."""


class McpAccessError(PermissionError):
    """Raised when a server or tool is not permitted by the config allowlist."""


class McpToolError(RuntimeError):
    """Raised when a permitted tool call returns an error result."""


@dataclass(frozen=True)
class McpServerSpec:
    """One allowlisted outbound MCP server.

    Parameters
    ----------
    name : str
        Stable server name referenced on the command line.
    command : str
        Executable launched for the stdio MCP server.
    args : tuple[str, ...]
        Arguments passed to the command.
    env : dict[str, str]
        Extra environment variables for the server process.
    cwd : str
        Working directory for the server process; blank uses the current one.
    allowed_tools : frozenset[str]
        Tool names this server may run, or ``{"*"}`` for every tool. Empty means
        no tool is permitted (deny by default).
    timeout_seconds : float
        Per-call timeout passed to the MCP session.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    allowed_tools: frozenset[str] = frozenset()
    timeout_seconds: float = 30.0


def tool_allowed(spec: McpServerSpec, tool: str) -> bool:
    """Return whether ``tool`` is permitted on ``spec`` (deny by default)."""
    return WILDCARD in spec.allowed_tools or tool in spec.allowed_tools


def load_outbound_config(path: str | Path) -> dict[str, McpServerSpec]:
    """Load and validate the outbound MCP server allowlist from a JSON file.

    The file is ``{"servers": [{"name", "command", "args"?, "env"?, "cwd"?,
    "allowed_tools"?, "timeout_seconds"?}, ...]}``.

    Returns
    -------
    dict[str, McpServerSpec]
        Server specs keyed by name.

    Raises
    ------
    McpConfigError
        When the file is missing, not JSON, or an entry is malformed.
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise McpConfigError(f"MCP config does not exist: {target}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"invalid MCP config JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise McpConfigError("MCP config must be an object with a 'servers' list")
    servers: dict[str, McpServerSpec] = {}
    for index, entry in enumerate(data["servers"]):
        spec = _parse_server(entry, index)
        if spec.name in servers:
            raise McpConfigError(f"duplicate MCP server name: {spec.name}")
        servers[spec.name] = spec
    return servers


def _parse_server(entry: object, index: int) -> McpServerSpec:
    """Parse one server entry into an :class:`McpServerSpec`."""
    if not isinstance(entry, dict):
        raise McpConfigError(f"MCP server entry {index} must be an object")
    name = str(entry.get("name", "")).strip()
    command = str(entry.get("command", "")).strip()
    if not name or not command:
        raise McpConfigError(f"MCP server entry {index} needs non-empty name and command")
    args = tuple(str(arg) for arg in entry.get("args", []))
    env = {str(key): str(value) for key, value in dict(entry.get("env", {})).items()}
    allowed = frozenset(str(tool) for tool in entry.get("allowed_tools", []))
    return McpServerSpec(
        name=name,
        command=command,
        args=args,
        env=env,
        cwd=str(entry.get("cwd", "")).strip(),
        allowed_tools=allowed,
        timeout_seconds=float(entry.get("timeout_seconds", 30.0)),
    )


class McpSession(Protocol):
    """The subset of the MCP client session this client uses."""

    async def list_tools(self) -> Any:
        """Return the server's advertised tools."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a tool and return its result."""


SessionOpener = Callable[[McpServerSpec], AbstractAsyncContextManager[McpSession]]


def _require_mcp(import_module: Callable[[str], Any] = importlib.import_module) -> tuple[Any, Any]:
    """Import the MCP client modules, or raise a clear install hint."""
    try:
        client_stdio = import_module("mcp.client.stdio")
        mcp_root = import_module("mcp")
    except ImportError as exc:
        raise RuntimeError(MCP_EXTRA_HINT) from exc
    return client_stdio, mcp_root


@asynccontextmanager
async def default_session_opener(spec: McpServerSpec) -> AsyncIterator[McpSession]:
    """Open and initialise a real stdio MCP session for ``spec``."""
    client_stdio, mcp_root = _require_mcp()
    params = client_stdio.StdioServerParameters(
        command=spec.command,
        args=list(spec.args),
        env=spec.env or None,
        cwd=spec.cwd or None,
    )
    async with client_stdio.stdio_client(params) as (read, write):
        async with mcp_root.ClientSession(read, write) as session:
            await session.initialize()
            yield session


def result_text(result: Any) -> str:
    """Join the text content blocks of an MCP tool result."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(parts)


class OutboundMcpClient:
    """A deny-by-default client for calling allowlisted external MCP tools."""

    def __init__(
        self,
        servers: dict[str, McpServerSpec],
        *,
        session_opener: SessionOpener = default_session_opener,
    ) -> None:
        self._servers = dict(servers)
        self._opener = session_opener

    def server_names(self) -> list[str]:
        """Return the sorted names of allowlisted servers."""
        return sorted(self._servers)

    def _spec(self, server: str) -> McpServerSpec:
        spec = self._servers.get(server)
        if spec is None:
            raise McpAccessError(f"server '{server}' is not in the outbound MCP allowlist")
        return spec

    async def list_tools(self, server: str) -> list[dict[str, str]]:
        """List the allowlisted tools a server advertises."""
        spec = self._spec(server)
        async with self._opener(spec) as session:
            listed = await session.list_tools()
        return [
            {"name": str(tool.name), "description": str(getattr(tool, "description", "") or "")}
            for tool in listed.tools
            if tool_allowed(spec, str(tool.name))
        ]

    async def call_tool(
        self, server: str, tool: str, arguments: dict[str, Any] | None = None
    ) -> str:
        """Call an allowlisted tool and return its joined text result.

        Raises
        ------
        McpAccessError
            When the server or tool is not permitted by the config.
        McpToolError
            When the permitted tool returns an error result.
        """
        spec = self._spec(server)
        if not tool_allowed(spec, tool):
            raise McpAccessError(f"tool '{tool}' on server '{server}' is not allowed by the config")
        async with self._opener(spec) as session:
            result = await session.call_tool(tool, arguments or {})
        if getattr(result, "isError", False):
            raise McpToolError(
                f"tool '{tool}' on '{server}' returned an error: {result_text(result)}"
            )
        return result_text(result)
