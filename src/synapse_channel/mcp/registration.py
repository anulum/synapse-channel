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

MCP_EXTRA_HINT = "The MCP face needs the optional extra: pip install 'synapse-channel[mcp]'"
"""Message shown when ``synapse mcp`` runs without the ``mcp`` SDK installed."""


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
) -> FastMCP:
    """Build a FastMCP server whose tools and resources delegate to ``bridge``.

    Parameters
    ----------
    bridge : SynapseHubBridge
        The translation layer holding the hub connection.

    Returns
    -------
    mcp.server.fastmcp.FastMCP
        A server exposing the coordination tools and read-only resources.

    Raises
    ------
    RuntimeError
        When the ``mcp`` SDK is not installed.
    """
    fast_mcp = fastmcp_loader()
    server = fast_mcp("synapse")

    @server.tool()
    async def synapse_claim(task_id: str, paths: list[str] | None = None) -> str:
        """Claim a task lease, optionally scoped to file paths."""
        return await bridge.claim(task_id, paths)

    @server.tool()
    async def synapse_release(task_id: str) -> str:
        """Release a task lease you hold."""
        return await bridge.release(task_id)

    @server.tool()
    async def synapse_send(target: str, message: str) -> str:
        """Send a chat message to an agent, a group, or everyone (``all``)."""
        return await bridge.send(target, message)

    @server.tool()
    async def synapse_handoff(task_id: str, to_agent: str) -> str:
        """Hand a held task to another online agent atomically."""
        return await bridge.handoff(task_id, to_agent)

    @server.tool()
    async def synapse_task_declare(
        task_id: str, title: str, depends_on: list[str] | None = None
    ) -> str:
        """Declare or refine a task on the shared plan."""
        return await bridge.task_declare(task_id, title, depends_on)

    @server.tool()
    async def synapse_task_update(
        task_id: str, status: str | None = None, suggested_owner: str | None = None
    ) -> str:
        """Update a plan task's status or suggested owner."""
        return await bridge.task_update(task_id, status, suggested_owner)

    @server.tool()
    async def synapse_board() -> str:
        """Return the shared task/progress blackboard as JSON."""
        return await bridge.board()

    @server.tool()
    async def synapse_state() -> str:
        """Return the live claims and checkpoints snapshot as JSON."""
        return await bridge.state()

    @server.tool()
    async def synapse_manifest() -> str:
        """Return the capability manifest of advertised agents as JSON."""
        return await bridge.manifest()

    @server.tool()
    async def synapse_directory() -> str:
        """Return the discovery-only capability directory as JSON."""
        return await bridge.directory()

    @server.tool()
    async def synapse_route_task(
        task_id: str,
        limit: int = 5,
        include_zero: bool = False,
        event_store: str | None = None,
    ) -> str:
        """Return advisory route recommendations for a board task as JSON."""
        return await bridge.route_task(task_id, limit, include_zero, event_store)

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
    ) -> str:
        """Return deterministic local memory recall hits as JSON."""
        return await bridge.memory_recall(event_store, query, limit, since_seq)

    @server.resource("synapse://board")
    async def board_resource() -> str:
        """Live shared task/progress blackboard."""
        return await bridge.board()

    @server.resource("synapse://state")
    async def state_resource() -> str:
        """Live claims and checkpoints snapshot."""
        return await bridge.state()

    @server.resource("synapse://manifest")
    async def manifest_resource() -> str:
        """Live capability manifest of advertised agents."""
        return await bridge.manifest()

    @server.resource("synapse://directory")
    async def directory_resource() -> str:
        """Live discovery-only capability directory."""
        return await bridge.directory()

    @server.resource("synapse://task/{task_id}")
    async def task_resource(task_id: str) -> str:
        """Read-only board task resource by task id."""
        return await bridge.task_resource(task_id)

    @server.resource("synapse://agent/{agent}")
    async def agent_resource(agent: str) -> str:
        """Read-only agent card and resource-offer resource by identity."""
        return await bridge.agent_resource(agent)

    @server.resource("synapse://resource-kind/{kind}")
    async def resource_kind_resource(kind: str) -> str:
        """Read-only resource-offer resource by resource kind."""
        return await bridge.resource_kind_resource(kind)

    return server
