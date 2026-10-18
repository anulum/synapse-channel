# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP tool and resource registration
"""FastMCP registration for Synapse coordination tools and resources."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.mcp.bridge import SynapseHubBridge

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from synapse_channel.mcp.http_views import ProjectMcpViews

MCP_EXTRA_HINT = "The MCP face needs the optional extra: pip install 'synapse-channel[mcp]'"
"""Message shown when ``synapse mcp`` runs without the ``mcp`` SDK installed."""

REQUIRED_MCP_CLAIM_TOOLS: frozenset[str] = frozenset(
    {
        "synapse_claim",
        "synapse_git_claim",
        "synapse_release",
    }
)
"""Claim-path tools every MCP face must expose for git-claim coordination."""


def registered_mcp_tool_names() -> frozenset[str]:
    """Return the tool names wired by :func:`build_mcp_server` (static inventory).

    Used by ``synapse doctor --mcp-policy`` so operators can confirm claim tools
    are registered without starting a live MCP session.

    Keep this frozenset in lock-step with the ``@server.tool`` registrations in
    :func:`build_mcp_server`. Tests assert equality against
    ``await server.list_tools()`` so the doctor inventory cannot silently drift.
    """
    return frozenset(
        {
            "synapse_claim",
            "synapse_git_claim",
            "synapse_release",
            "synapse_send",
            "synapse_inbox",
            "synapse_handoff",
            "synapse_task_declare",
            "synapse_task_update",
            "synapse_board",
            "synapse_status",
            "synapse_state",
            "synapse_manifest",
            "synapse_directory",
            "synapse_route_task",
            "synapse_resource_bids",
            "synapse_memory_recall",
            "synapse_entitlements",
            "synapse_app_task_offer",
            "synapse_app_task_status",
            "synapse_app_task_attach",
        }
    )


def _require_fastmcp(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> type[FastMCP]:
    """Import and return :class:`FastMCP`, or raise a clear install hint.

    The ``mcp`` SDK is an optional extra, so the import lives behind this helper
    rather than at module import time. The bridge and CLI remain importable
    without the extra.

    Returns
    -------
    type
        The ``mcp.server.fastmcp.FastMCP`` class.

    Raises
    ------
    RuntimeError
        When the ``mcp`` SDK is not installed, with the extra to install.
    """
    try:
        module = import_module("mcp.server.fastmcp")
    except ImportError as exc:
        raise RuntimeError(MCP_EXTRA_HINT) from exc
    return cast("type[FastMCP]", module.FastMCP)


def build_mcp_server(
    bridge: SynapseHubBridge,
    *,
    fastmcp_loader: Callable[[], type[FastMCP]] = _require_fastmcp,
    server: FastMCP | None = None,
    views: ProjectMcpViews | None = None,
) -> FastMCP:
    """Build a FastMCP server whose tools and resources delegate to ``bridge``.

    Parameters
    ----------
    bridge : SynapseHubBridge
        The translation layer holding the hub connection.
    server : FastMCP or None, optional
        Configured transport server. Omission creates the original stdio server.
    views : ProjectMcpViews or None, optional
        Project-scoped remote read views; mutations still use the original bridge.

    Returns
    -------
    mcp.server.fastmcp.FastMCP
        A server exposing the coordination tools and read-only resources.

    Raises
    ------
    RuntimeError
        When the ``mcp`` SDK is not installed.
    """
    if server is None:
        fast_mcp = fastmcp_loader()
        server = fast_mcp("synapse")
    reader = views if views is not None else bridge
    from synapse_channel.mcp.outcome_tools import register_mutation_tools

    register_mutation_tools(server, bridge)

    @server.tool()
    async def synapse_inbox(limit: int = 50) -> str:
        """Consume a bounded page of local durable messages for this bridge identity."""
        return await bridge.inbox(limit)

    @server.tool()
    async def synapse_board() -> str:
        """Return the shared task/progress blackboard as JSON."""
        return await reader.board()

    @server.tool()
    async def synapse_status() -> str:
        """Return live presence, waiter, work, resource, and mailbox counts as JSON."""
        return await reader.status()

    @server.tool()
    async def synapse_state() -> str:
        """Return the live claims and checkpoints snapshot as JSON."""
        return await reader.state()

    @server.tool()
    async def synapse_manifest() -> str:
        """Return the capability manifest of advertised agents as JSON."""
        return await reader.manifest()

    @server.tool()
    async def synapse_directory() -> str:
        """Return the discovery-only capability directory as JSON."""
        return await reader.directory()

    @server.tool()
    async def synapse_route_task(
        task_id: str,
        limit: int = 5,
        include_zero: bool = False,
        event_store: str | None = None,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return advisory route recommendations for a board task as JSON."""
        return await bridge.route_task(
            task_id,
            limit,
            include_zero,
            event_store,
            event_store_key_file=event_store_key_file,
        )

    @server.tool()
    async def synapse_resource_bids(
        task_id: str,
        resource_kind: str | None = None,
        limit: int = 5,
        include_zero: bool = False,
    ) -> str:
        """Return advisory resource bids for a board task as JSON."""
        return await bridge.resource_bids(task_id, resource_kind, limit, include_zero)

    @server.tool()
    async def synapse_memory_recall(
        event_store: str,
        query: str,
        limit: int = 5,
        since_seq: int = 0,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return deterministic local memory recall hits as JSON."""
        return await bridge.memory_recall(
            event_store,
            query,
            limit,
            since_seq,
            event_store_key_file=event_store_key_file,
        )

    @server.tool()
    async def synapse_entitlements() -> str:
        """Return a redacted local account-ledger overview without spending authority."""
        return await bridge.entitlements()

    @server.tool()
    async def synapse_app_task_status(task_id: str) -> str:
        """Read redacted state for one human app task."""
        return await bridge.app_task_status(task_id)

    @server.resource("synapse://board")
    async def board_resource() -> str:
        """Live shared task/progress blackboard."""
        return await reader.board()

    @server.resource("synapse://state")
    async def state_resource() -> str:
        """Live claims and checkpoints snapshot."""
        return await reader.state()

    @server.resource("synapse://manifest")
    async def manifest_resource() -> str:
        """Live capability manifest of advertised agents."""
        return await reader.manifest()

    @server.resource("synapse://directory")
    async def directory_resource() -> str:
        """Live discovery-only capability directory."""
        return await reader.directory()

    @server.resource("synapse://task/{task_id}")
    async def task_resource(task_id: str) -> str:
        """Read-only board task resource by task id."""
        return await reader.task_resource(task_id)

    @server.resource("synapse://agent/{agent}")
    async def agent_resource(agent: str) -> str:
        """Read-only agent card and resource-offer resource by identity."""
        return await reader.agent_resource(agent)

    @server.resource("synapse://resource-kind/{kind}")
    async def resource_kind_resource(kind: str) -> str:
        """Read-only resource-offer resource by resource kind."""
        return await reader.resource_kind_resource(kind)

    return server
