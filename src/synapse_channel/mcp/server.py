# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Model Context Protocol face over stdio, bridging to the hub
"""Model Context Protocol (MCP) server that bridges the hub to any MCP client.

The ``synapse mcp`` command runs an MCP server over stdio that is itself a
*client* of the hub: it holds one :class:`~synapse_channel.client.agent.SynapseAgent`
connection and re-exposes the coordination verbs as MCP **tools** (claim,
release, send, handoff, declare/update a task) and read-only **resources**
(the board, the state snapshot, the capability manifest). Any MCP-compatible
agent — Claude Desktop/Code, an editor assistant — coordinates through Synapse
by adding one server entry, with no Synapse-specific code.

The hub itself stays MCP-agnostic: this module is a separate adapter process,
not a hub change, and the ``mcp`` SDK is an optional extra
(``pip install 'synapse-channel[mcp]'``) so the core install keeps its single
``websockets`` dependency. :class:`SynapseHubBridge` holds no MCP dependency at
all — it is the testable translation layer between MCP tool calls and the
fire-and-forget agent, correlating each request with the hub's reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

AgentFactory = Callable[..., SynapseAgent]
"""Factory that builds the bridge's hub client; injectable for testing."""

Matcher = Callable[[dict[str, Any]], bool]
"""Predicate that selects the hub reply a pending request is waiting for."""

Sender = Callable[[], Awaitable[None]]
"""Zero-argument coroutine that issues one request on the hub client."""

DEFAULT_BRIDGE_NAME = "synapse-mcp"
"""Default identity the MCP adapter registers under on the hub."""

DEFAULT_REQUEST_TIMEOUT = 5.0
"""Seconds a tool waits for the hub's reply before reporting no response."""

MCP_EXTRA_HINT = "The MCP face needs the optional extra: pip install 'synapse-channel[mcp]'"
"""Message shown when ``synapse mcp`` runs without the ``mcp`` SDK installed."""


def _require_fastmcp() -> type[FastMCP]:
    """Import and return :class:`FastMCP`, or raise a clear install hint.

    The ``mcp`` SDK is an optional extra, so the import lives behind this helper
    rather than at module import time — the module (and :class:`SynapseHubBridge`)
    stay importable, and the CLI can wire the subcommand, without the extra.

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
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(MCP_EXTRA_HINT) from exc
    return FastMCP


class SynapseHubBridge:
    """Translate MCP tool/resource calls into hub coordination verbs.

    Holds one hub client and a list of pending requests. Each query/action sends
    on the (fire-and-forget) client and awaits the hub's correlated reply through
    :meth:`on_message`, which the client invokes for every inbound message. This
    class has no MCP dependency, so the whole translation layer is unit-testable
    with a fake agent.

    Parameters
    ----------
    uri : str, optional
        Hub WebSocket URI. Defaults to :data:`~synapse_channel.client.agent.DEFAULT_HUB_URI`.
    name : str, optional
        Identity registered on the hub. Defaults to :data:`DEFAULT_BRIDGE_NAME`.
    token : str or None, optional
        Shared-secret token for a secured hub.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    request_timeout : float, optional
        Seconds to await a hub reply before reporting no response. Defaults to
        :data:`DEFAULT_REQUEST_TIMEOUT`.
    """

    def __init__(
        self,
        *,
        uri: str = DEFAULT_HUB_URI,
        name: str = DEFAULT_BRIDGE_NAME,
        token: str | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.name = name
        self.request_timeout = request_timeout
        self._waiters: list[tuple[Matcher, asyncio.Future[dict[str, Any]]]] = []
        self.agent = agent_factory(name, self.on_message, uri=uri, verbose=False, token=token)

    async def on_message(self, data: dict[str, Any]) -> None:
        """Resolve the first pending request whose matcher accepts ``data``.

        Registered as the hub client's callback, so it sees every inbound message
        and hands each to at most one waiting request.

        Parameters
        ----------
        data : dict[str, Any]
            One decoded inbound message from the hub.
        """
        for waiter in list(self._waiters):
            match, future = waiter
            if not future.done() and match(data):
                future.set_result(data)
                with contextlib.suppress(ValueError):
                    self._waiters.remove(waiter)
                return

    async def _await_reply(self, match: Matcher, send: Sender) -> dict[str, Any] | None:
        """Register a matcher, issue ``send``, and return the correlated reply.

        Parameters
        ----------
        match : Matcher
            Predicate selecting the hub reply this request waits for.
        send : Sender
            Coroutine that issues the request on the hub client.

        Returns
        -------
        dict[str, Any] or None
            The matched reply, or ``None`` if none arrived within
            :attr:`request_timeout`.
        """
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        waiter = (match, future)
        self._waiters.append(waiter)
        try:
            await send()
            return await asyncio.wait_for(future, self.request_timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    async def claim(self, task_id: str, paths: list[str] | None = None) -> str:
        """Claim a task lease, optionally scoped to ``paths``, and report the outcome.

        Parameters
        ----------
        task_id : str
            Task identifier to lease.
        paths : list[str] or None, optional
            File/directory paths the claim intends to touch; empty claims the
            whole worktree.

        Returns
        -------
        str
            A human-readable grant, denial, or no-response line.
        """
        scope = list(paths or [])

        def match(data: dict[str, Any]) -> bool:
            if data.get("task_id") != task_id:
                return False
            kind = data.get("type")
            if kind == MessageType.CLAIM_GRANTED:
                return data.get("owner") == self.name
            return kind == MessageType.CLAIM_DENIED

        reply = await self._await_reply(match, lambda: self.agent.claim(task_id, paths=scope))
        if reply is None:
            return f"claim '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.CLAIM_GRANTED:
            where = ", ".join(scope) if scope else "the whole worktree"
            return f"claim granted: '{task_id}' ({where})"
        return f"claim denied: '{task_id}' — {reply.get('payload') or 'held by another agent'}"

    async def release(self, task_id: str) -> str:
        """Release a held task lease and report the outcome.

        Parameters
        ----------
        task_id : str
            Identifier of the lease to release.

        Returns
        -------
        str
            A human-readable release, denial, or no-response line.
        """

        def match(data: dict[str, Any]) -> bool:
            return data.get("task_id") == task_id and data.get("type") in {
                MessageType.RELEASE_GRANTED,
                MessageType.RELEASE_DENIED,
            }

        reply = await self._await_reply(match, lambda: self.agent.release(task_id))
        if reply is None:
            return f"release '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.RELEASE_GRANTED:
            return f"released '{task_id}'"
        return f"release denied: '{task_id}' — {reply.get('payload') or 'not the owner'}"

    async def send(self, target: str, message: str) -> str:
        """Send one chat message to an agent or the room (fire-and-forget).

        Parameters
        ----------
        target : str
            Recipient agent name, a group glob, or ``"all"``.
        message : str
            Message body.

        Returns
        -------
        str
            Confirmation that the message was sent.
        """
        await self.agent.chat(message, target=target)
        return f"sent to {target}"

    async def handoff(self, task_id: str, to_agent: str) -> str:
        """Hand a held task to another agent in one atomic step.

        Parameters
        ----------
        task_id : str
            Identifier of the held task.
        to_agent : str
            The agent to receive the task; must be online.

        Returns
        -------
        str
            A human-readable handoff, denial, or no-response line.
        """

        def match(data: dict[str, Any]) -> bool:
            return data.get("task_id") == task_id and data.get("type") in {
                MessageType.HANDOFF_GRANTED,
                MessageType.HANDOFF_DENIED,
            }

        reply = await self._await_reply(match, lambda: self.agent.handoff(task_id, to_agent))
        if reply is None:
            return f"handoff '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.HANDOFF_GRANTED:
            return f"handed off '{task_id}' to {to_agent}"
        return f"handoff denied: '{task_id}' — {reply.get('payload') or 'rejected'}"

    async def task_declare(
        self, task_id: str, title: str, depends_on: list[str] | None = None
    ) -> str:
        """Declare (or refine) a task on the shared plan.

        Parameters
        ----------
        task_id : str
            Stable task identifier.
        title : str
            Short human-readable name of the work.
        depends_on : list[str] or None, optional
            Prerequisite task ids; the hub refuses a dependency cycle.

        Returns
        -------
        str
            A confirmation, or a no-response line.
        """
        deps = tuple(depends_on or ())

        def match(data: dict[str, Any]) -> bool:
            return (
                data.get("type") == MessageType.LEDGER_TASK_POSTED
                and data.get("task", {}).get("task_id") == task_id
            )

        reply = await self._await_reply(
            match, lambda: self.agent.post_task(task_id, title=title, depends_on=deps)
        )
        if reply is None:
            return f"declare '{task_id}': no response from the hub"
        task = reply.get("task", {})
        return f"declared '{task_id}' — {task.get('title')}"

    async def task_update(
        self, task_id: str, status: str | None = None, suggested_owner: str | None = None
    ) -> str:
        """Update a plan task's status or suggested owner.

        Parameters
        ----------
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New planning status (``open``/``in_progress``/``blocked``/``done``/
            ``cancelled``); an unknown status is refused.
        suggested_owner : str or None, optional
            Replacement advisory owner.

        Returns
        -------
        str
            A confirmation, or a no-response line.
        """

        def match(data: dict[str, Any]) -> bool:
            return (
                data.get("type") == MessageType.LEDGER_TASK_UPDATED
                and data.get("task", {}).get("task_id") == task_id
            )

        reply = await self._await_reply(
            match,
            lambda: self.agent.update_ledger_task(
                task_id, status=status, suggested_owner=suggested_owner
            ),
        )
        if reply is None:
            return f"update '{task_id}': no response from the hub"
        task = reply.get("task", {})
        return f"updated '{task_id}' -> status={task.get('status')}"

    @staticmethod
    def _render(reply: dict[str, Any] | None, key: str, on_timeout: str) -> str:
        """Return the ``key`` field of ``reply`` as indented JSON, or ``on_timeout``."""
        if reply is None:
            return on_timeout
        return json.dumps(reply.get(key, {}), indent=2, sort_keys=True)

    async def board(self) -> str:
        """Return the shared task/progress blackboard as JSON.

        Returns
        -------
        str
            The board snapshot as indented JSON, or a no-response line.
        """
        reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        return self._render(reply, "board", "the hub did not return the board")

    async def state(self) -> str:
        """Return the live claims/checkpoints snapshot as JSON.

        Returns
        -------
        str
            The state snapshot as indented JSON, or a no-response line.
        """
        reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        return self._render(reply, "snapshot", "the hub did not return its state")

    async def manifest(self) -> str:
        """Return the capability manifest of advertised agents as JSON.

        Returns
        -------
        str
            The manifest as indented JSON, or a no-response line.
        """
        reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        return self._render(reply, "manifest", "the hub did not return the manifest")


def build_mcp_server(bridge: SynapseHubBridge) -> FastMCP:
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
    fast_mcp = _require_fastmcp()
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

    return server


async def serve_stdio(
    *,
    uri: str = DEFAULT_HUB_URI,
    name: str = DEFAULT_BRIDGE_NAME,
    token: str | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ready_timeout: float = 5.0,
    agent_factory: AgentFactory = SynapseAgent,
    server_builder: Callable[[SynapseHubBridge], Any] = build_mcp_server,
) -> int:
    """Connect to the hub and run the MCP server over stdio until the client closes.

    Parameters
    ----------
    uri : str, optional
        Hub WebSocket URI.
    name : str, optional
        Identity to register on the hub.
    token : str or None, optional
        Shared-secret token for a secured hub.
    request_timeout : float, optional
        Seconds to await a hub reply before reporting no response. Defaults to
        :data:`DEFAULT_REQUEST_TIMEOUT`.
    ready_timeout : float, optional
        Seconds to wait for the bridge agent handshake before reporting the hub
        unreachable. Defaults to ``5.0``.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    server_builder : Callable, optional
        Builds the MCP server from the bridge; injectable for testing.

    Returns
    -------
    int
        ``0`` once the MCP client disconnects, ``1`` when the hub is unreachable.
    """
    bridge = SynapseHubBridge(
        uri=uri,
        name=name,
        token=token,
        request_timeout=request_timeout,
        agent_factory=agent_factory,
    )
    conn_task = asyncio.create_task(bridge.agent.connect())
    try:
        if not await bridge.agent.wait_until_ready(timeout=ready_timeout):
            print(f"[{name}] could not reach hub at {uri}", file=sys.stderr)
            return 1
        server = server_builder(bridge)
        await server.run_stdio_async()
        return 0
    finally:
        bridge.agent.running = False
        conn_task.cancel()
