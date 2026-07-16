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

Configuration schema and trust enforcement live in dedicated modules; the
actual MCP session is opened through an injectable opener so the orchestration is
testable without a live subprocess. The ``mcp`` SDK is an optional extra
(``pip install 'synapse-channel[mcp]'``); importing this module never requires it.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.mcp_config import (
    WILDCARD as WILDCARD,
)
from synapse_channel.core.mcp_config import (
    McpConfigError as McpConfigError,
)
from synapse_channel.core.mcp_config import (
    McpServerSpec as McpServerSpec,
)
from synapse_channel.core.mcp_config import (
    tool_allowed as tool_allowed,
)
from synapse_channel.core.mcp_config_launch import (
    MCP_SDK_POSIX_DEFAULT_ENV,
    bind_mcp_server_launch,
    child_environment,
)
from synapse_channel.core.mcp_config_trust import load_trusted_mcp_config

MCP_EXTRA_HINT = "outbound MCP calls need the optional extra: pip install 'synapse-channel[mcp]'"
MCP_SDK_VERSION = "1.28.1"
"""Exact SDK release whose stdio environment merge contract is enforced."""

MCP_SDK_TERMINATION_TIMEOUT = 2.0
"""Audited SDK grace window before process-tree force termination."""


class McpAccessError(SynapseError, PermissionError):
    """Raised when a server or tool is not permitted by the config allowlist."""

    code = "mcp_access"


class McpToolError(SynapseError, RuntimeError):
    """Raised when a permitted tool call returns an error result."""

    code = "mcp_tool"


def load_outbound_config(
    path: str | Path,
    *,
    trust_bundle_path: str | Path | None = None,
    allow_repo_config: bool = False,
    repository_root: str | Path | None = None,
) -> dict[str, McpServerSpec]:
    """Load a filesystem- and signature-validated outbound MCP policy.

    By default the config must be an owner-only, no-follow regular file outside
    the active repository. Supplying ``trust_bundle_path`` also requires a valid
    Ed25519 manifest signature. Every server command is an absolute executable
    path, revalidated immediately before launch, and may carry a SHA-256 pin.

    Returns
    -------
    dict[str, McpServerSpec]
        Server specs keyed by name.

    Raises
    ------
    McpConfigError
        When file provenance, JSON schema, signature, executable, or working
        directory policy fails.
    """
    servers, _report = load_trusted_mcp_config(
        path,
        trust_bundle_path=trust_bundle_path,
        allow_repo_config=allow_repo_config,
        repository_root=repository_root,
    )
    return servers


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


def _verify_mcp_sdk_contract(client_stdio: Any, *, installed_version: str | None = None) -> None:
    """Fail closed unless the installed SDK matches the audited stdio contract."""
    actual_version = distribution_version("mcp") if installed_version is None else installed_version
    inherited_names = tuple(getattr(client_stdio, "DEFAULT_INHERITED_ENV_VARS", ()))
    termination_timeout = getattr(client_stdio, "PROCESS_TERMINATION_TIMEOUT", None)
    if (
        actual_version != MCP_SDK_VERSION
        or inherited_names != MCP_SDK_POSIX_DEFAULT_ENV
        or termination_timeout != MCP_SDK_TERMINATION_TIMEOUT
    ):
        raise McpConfigError(
            "unsupported MCP SDK stdio environment contract: expected "
            f"mcp=={MCP_SDK_VERSION}, defaults {MCP_SDK_POSIX_DEFAULT_ENV!r}, and "
            f"termination timeout {MCP_SDK_TERMINATION_TIMEOUT:g}s"
        )


@asynccontextmanager
async def default_session_opener(spec: McpServerSpec) -> AsyncIterator[McpSession]:
    """Revalidate, open, and initialise a real stdio MCP session for ``spec``."""
    client_stdio, mcp_root = _require_mcp()
    _verify_mcp_sdk_contract(client_stdio)
    with bind_mcp_server_launch(spec) as launch:
        params = client_stdio.StdioServerParameters(
            command=launch.command,
            args=list(spec.args),
            env=child_environment(spec),
            cwd=launch.cwd,
        )
        async with client_stdio.stdio_client(params, errlog=sys.stderr) as (read, write):
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

        async def discover() -> Any:
            async with self._opener(spec) as session:
                return await session.list_tools()

        try:
            listed = await asyncio.wait_for(discover(), timeout=spec.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpToolError(
                f"tool discovery on server '{server}' timed out after "
                f"{spec.timeout_seconds:g} seconds"
            ) from exc
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

        async def call() -> Any:
            async with self._opener(spec) as session:
                return await session.call_tool(tool, arguments or {})

        try:
            result = await asyncio.wait_for(call(), timeout=spec.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpToolError(
                f"tool '{tool}' on server '{server}' timed out after "
                f"{spec.timeout_seconds:g} seconds"
            ) from exc
        if getattr(result, "isError", False):
            raise McpToolError(
                f"tool '{tool}' on '{server}' returned an error: {result_text(result)}"
            )
        return result_text(result)
