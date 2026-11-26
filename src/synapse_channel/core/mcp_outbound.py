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
from synapse_channel.core.frame_canonical import FrameCanonicalError
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
from synapse_channel.core.mcp_tool_pins import tool_descriptor_bytes, tool_descriptor_digest

MCP_EXTRA_HINT = "outbound MCP calls need the optional extra: pip install 'synapse-channel[mcp]'"
MCP_SDK_VERSION = "2.3.0"
"""Exact SDK release whose stdio environment merge contract is enforced."""

MCP_SDK_TERMINATION_TIMEOUT = 2.0
"""Audited SDK grace window before process-tree force termination."""

MCP_TOOL_LIST_MAX_PAGES = 32
"""The most ``tools/list`` pages one discovery reads.

A server that needs more pages is refused, never truncated.
"""


class McpAccessError(SynapseError, PermissionError):
    """Raised when a server or tool is not permitted by the config allowlist."""

    code = "mcp_access"


class McpToolDescriptorError(McpAccessError):
    """Raised when a pinned tool's advertised descriptor no longer matches its pin.

    A drift is a possible rug pull, so it carries its own code for operators and machine
    readers, while the CLI exit code stays the access-refusal one.
    """

    code = "mcp_tool_descriptor"


CHANGED_DESCRIPTION = "<descriptor changed; not shown>"
"""What ``mcp-tools`` shows instead of a changed tool's live, now untrusted, description."""


class McpToolError(SynapseError, RuntimeError):
    """Raised when a permitted tool call returns an error result."""

    code = "mcp_tool"


class McpDependencyError(SynapseError, RuntimeError):
    """Raised when the optional outbound MCP dependency is unavailable."""

    code = "mcp_dependency"


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

    async def list_tools(self, *, params: Any = None) -> Any:
        """Return one page of the server's advertised tools, after ``params.cursor`` when given."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a tool and return its result."""


SessionOpener = Callable[[McpServerSpec], AbstractAsyncContextManager[McpSession]]


def _require_mcp(import_module: Callable[[str], Any] = importlib.import_module) -> tuple[Any, Any]:
    """Import the MCP client modules, or raise a clear install hint."""
    try:
        client_stdio = import_module("mcp.client.stdio")
        mcp_root = import_module("mcp")
    except ImportError as exc:
        raise McpDependencyError(MCP_EXTRA_HINT) from exc
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


def _paginated_params(cursor: str) -> Any:
    """Return the SDK's pagination parameters that ask for the page after ``cursor``."""
    try:
        types = importlib.import_module("mcp.types")
    except ImportError as exc:
        raise McpDependencyError(MCP_EXTRA_HINT) from exc
    return types.PaginatedRequestParams(cursor=cursor)


async def _list_all_tools(session: McpSession, server: str) -> list[Any]:
    """Read every page of a server's ``tools/list``, following its cursor.

    Parameters
    ----------
    session : McpSession
        An opened session.
    server : str
        The server's configured name, for the error message.

    Returns
    -------
    list[Any]
        Every advertised tool, in page order.

    Raises
    ------
    McpToolError
        When a cursor repeats (a looping listing) or the server needs more than
        :data:`MCP_TOOL_LIST_MAX_PAGES` pages. The listing is refused, never truncated.
    """
    tools: list[Any] = []
    seen: set[str] = set()
    cursor: str | None = None
    for _page in range(MCP_TOOL_LIST_MAX_PAGES):
        if cursor is None:
            listed = await session.list_tools()
        else:
            listed = await session.list_tools(params=_paginated_params(cursor))
        tools.extend(listed.tools)
        following = getattr(listed, "next_cursor", None)
        if following is None:
            return tools
        cursor = str(following)
        if cursor in seen:
            raise McpToolError(
                f"tool discovery on server '{server}' returned a cursor it had already returned; "
                "refusing a looping listing"
            )
        seen.add(cursor)
    raise McpToolError(
        f"tool discovery on server '{server}' needs more than {MCP_TOOL_LIST_MAX_PAGES} pages; "
        "refused rather than truncated"
    )


def _named(tools: list[Any], tool: str) -> list[Any]:
    """Return every advertised tool whose name is exactly ``tool``."""
    return [entry for entry in tools if str(entry.name) == tool]


def _live_digest(entry: Any, server: str, tool: str) -> str:
    """Return one advertised tool's descriptor digest, refusing one that cannot be pinned."""
    try:
        return tool_descriptor_digest(entry)
    except FrameCanonicalError as exc:
        raise McpToolDescriptorError(
            f"tool '{tool}' on server '{server}' advertises a descriptor that cannot be pinned"
        ) from exc


def _verify_pinned_tool(spec: McpServerSpec, server: str, tool: str, tools: list[Any]) -> None:
    """Refuse a call unless the pinned tool is advertised once, with its pinned descriptor.

    The message names the server, the tool and the first 12 hexadecimal characters of both
    digests. It never echoes the live description, which is untrusted once it has changed.
    """
    named = _named(tools, tool)
    if not named:
        raise McpToolDescriptorError(
            f"pinned tool '{tool}' is no longer advertised by server '{server}'"
        )
    if len(named) > 1:
        raise McpToolDescriptorError(
            f"server '{server}' advertises pinned tool '{tool}' more than once; refusing the call"
        )
    pinned = spec.tool_pins[tool]
    live = _live_digest(named[0], server, tool)
    if live != pinned:
        raise McpToolDescriptorError(
            f"tool '{tool}' on server '{server}' changed its descriptor since it was pinned "
            f"(pinned {pinned[:12]}, advertised {live[:12]}); refusing the call"
        )


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

    def pinned(self, server: str) -> bool:
        """Return whether ``server``'s tools are pinned by descriptor digest."""
        return bool(self._spec(server).tool_pins)

    async def _discover(self, spec: McpServerSpec, server: str) -> list[Any]:
        """Return every tool the server advertises, under the server's deadline."""

        async def discover() -> list[Any]:
            async with self._opener(spec) as session:
                return await _list_all_tools(session, server)

        try:
            return await asyncio.wait_for(discover(), timeout=spec.timeout_seconds)
        except TimeoutError as exc:
            raise McpToolError(
                f"tool discovery on server '{server}' timed out after "
                f"{spec.timeout_seconds:g} seconds"
            ) from exc
        except (McpAccessError, McpConfigError, McpToolError):
            raise
        except McpDependencyError:
            raise
        except Exception as exc:
            raise _transport_error("tool discovery", server) from exc

    async def list_tools(self, server: str) -> list[dict[str, str]]:
        """List the allowlisted tools a server advertises, across every page of its listing.

        Each entry carries ``pin``: ``"unpinned"`` for a server without pins, otherwise
        ``"ok"``, ``"changed"`` (its description is then withheld) or ``"missing"`` (pinned but
        no longer advertised, listed from the pin).
        """
        spec = self._spec(server)
        listed = await self._discover(spec, server)
        if not spec.tool_pins:
            return [
                {"name": str(tool.name), "description": _description(tool), "pin": "unpinned"}
                for tool in listed
                if tool_allowed(spec, str(tool.name))
            ]
        entries: list[dict[str, str]] = []
        for name in sorted(spec.tool_pins):
            named = _named(listed, name)
            if not named:
                entries.append({"name": name, "description": "", "pin": "missing"})
                continue
            try:
                _verify_pinned_tool(spec, server, name, listed)
            except McpToolDescriptorError:
                entries.append({"name": name, "description": CHANGED_DESCRIPTION, "pin": "changed"})
                continue
            entries.append({"name": name, "description": _description(named[0]), "pin": "ok"})
        return entries

    async def pin_descriptors(
        self, server: str, tools: tuple[str, ...] = ()
    ) -> list[tuple[str, bytes, str]]:
        """Return the canonical descriptor bytes and digest of advertised tools.

        Parameters
        ----------
        server : str
            An allowlisted server.
        tools : tuple[str, ...], optional
            The tools to describe; every advertised tool when empty.

        Returns
        -------
        list[tuple[str, bytes, str]]
            ``(name, canonical bytes, digest)`` per tool, in name order.

        Raises
        ------
        McpToolDescriptorError
            When a requested tool is not advertised exactly once, or cannot be pinned.
        """
        spec = self._spec(server)
        listed = await self._discover(spec, server)
        names = sorted(set(tools)) if tools else sorted({str(entry.name) for entry in listed})
        described: list[tuple[str, bytes, str]] = []
        for name in names:
            named = _named(listed, name)
            if len(named) != 1:
                raise McpToolDescriptorError(
                    f"tool '{name}' is advertised {len(named)} times by server '{server}'; "
                    "it can be pinned only when advertised exactly once"
                )
            digest = _live_digest(named[0], server, name)
            described.append((name, tool_descriptor_bytes(named[0]), digest))
        return described

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
                if spec.tool_pins:
                    # List and verify in the same session, so the SDK checks the result
                    # against the pinned output schema, not a listing made after the call.
                    _verify_pinned_tool(spec, server, tool, await _list_all_tools(session, server))
                return await session.call_tool(tool, arguments or {})

        try:
            result = await asyncio.wait_for(call(), timeout=spec.timeout_seconds)
        except TimeoutError as exc:
            raise McpToolError(
                f"tool '{tool}' on server '{server}' timed out after "
                f"{spec.timeout_seconds:g} seconds"
            ) from exc
        except (McpAccessError, McpConfigError, McpToolError):
            raise
        except McpDependencyError:
            raise
        except Exception as exc:
            raise _transport_error(f"tool {tool!r}", server) from exc
        # SDK 2 names the flag ``is_error``. A result without a boolean flag is
        # refused rather than read as success, so a renamed field cannot fail open.
        is_error = getattr(result, "is_error", None)
        if not isinstance(is_error, bool):
            raise McpToolError(f"tool '{tool}' on '{server}' returned a malformed result")
        if is_error:
            raise McpToolError(
                f"tool '{tool}' on '{server}' returned an error: {result_text(result)}"
            )
        return result_text(result)


def _description(tool: Any) -> str:
    """Return a tool's advertised description, empty when it has none."""
    return str(getattr(tool, "description", "") or "")


def _transport_error(operation: str, server: str) -> McpToolError:
    """Return a stable error that never reflects subprocess diagnostics."""
    return McpToolError(f"{operation} on server {server!r} failed during MCP startup or transport")
