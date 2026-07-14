# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP hub translation bridge
"""Hub translation layer for the Model Context Protocol face."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.core.capability_directory import build_capability_directory, directory_to_json
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.advisory_actions import McpAdvisoryActions
from synapse_channel.mcp.claim_actions import McpClaimActions
from synapse_channel.mcp.inbox import DEFAULT_MCP_INBOX_LIMIT, McpFeedInbox
from synapse_channel.mcp.plan_actions import McpPlanActions
from synapse_channel.mcp.resource_views import (
    agent_resource_to_json,
    resource_kind_resource_to_json,
    task_resource_to_json,
)
from synapse_channel.mcp.status import mcp_status

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
    roles : Iterable[str], optional
        Additional full role names the bridge answers to and includes in its
        local durable inbox filter.
    inbox_feed, inbox_cursor : str, pathlib.Path, or None, optional
        Local relay feed and per-identity byte cursor. ``None`` uses the
        ``SYN_HOME``/home defaults owned by :class:`McpFeedInbox`.
    """

    def __init__(
        self,
        *,
        uri: str = DEFAULT_HUB_URI,
        name: str = DEFAULT_BRIDGE_NAME,
        token: str | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        roles: Iterable[str] = (),
        inbox_feed: str | Path | None = None,
        inbox_cursor: str | Path | None = None,
    ) -> None:
        self.name = name
        self.request_timeout = request_timeout
        role_names = tuple(dict.fromkeys(role.strip() for role in roles if role.strip()))
        self._waiters: list[tuple[Matcher, asyncio.Future[dict[str, Any]]]] = []
        self.inbox_reader = McpFeedInbox(
            name,
            roles=role_names,
            feed_path=inbox_feed,
            cursor_path=inbox_cursor,
        )
        self.agent = agent_factory(
            name,
            self.on_message,
            uri=uri,
            verbose=False,
            token=token,
            roles=role_names,
        )

        # Look up ``self._await_reply`` on each call so test doubles that rebind
        # the method on the bridge still reach the action facades.
        async def await_reply(match: Matcher, send: Sender) -> dict[str, Any] | None:
            return await self._await_reply(match, send)

        self.claim_actions = McpClaimActions(self.name, self.agent, await_reply)
        self.plan_actions = McpPlanActions(self.agent, await_reply)
        self.advisory_actions = McpAdvisoryActions(self.agent, await_reply)

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
        """Claim a task lease, optionally scoped to ordinary paths."""
        return await self.claim_actions.claim(task_id, paths)

    async def git_claim(
        self,
        task_id: str,
        paths: Sequence[str] | None = None,
        *,
        base: str = "main",
        auto_release_on: str = "manual",
        whole_worktree: bool = False,
    ) -> str:
        """Claim bounded paths in the MCP process's current Git worktree."""
        return await self.claim_actions.git_claim(
            task_id,
            paths,
            base=base,
            auto_release_on=auto_release_on,
            whole_worktree=whole_worktree,
        )

    async def release(
        self,
        task_id: str,
        *,
        evidence: Sequence[str] = (),
        changed_files: Sequence[str] = (),
        confidence: str = "",
    ) -> str:
        """Release a held task lease with optional receipt evidence."""
        return await self.claim_actions.release(
            task_id,
            evidence=evidence,
            changed_files=changed_files,
            confidence=confidence,
        )

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
        """Hand a held task to another agent in one atomic step."""
        return await self.plan_actions.handoff(task_id, to_agent)

    async def task_declare(
        self, task_id: str, title: str, depends_on: list[str] | None = None
    ) -> str:
        """Declare (or refine) a task on the shared plan."""
        return await self.plan_actions.task_declare(task_id, title, depends_on)

    async def task_update(
        self, task_id: str, status: str | None = None, suggested_owner: str | None = None
    ) -> str:
        """Update a plan task's status or suggested owner."""
        return await self.plan_actions.task_update(task_id, status, suggested_owner)

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

    async def inbox(self, limit: int = DEFAULT_MCP_INBOX_LIMIT) -> str:
        """Return one bounded, cursored page of local durable message bodies."""
        return self.inbox_reader.drain(limit)

    async def status(self) -> str:
        """Return live roster, waiter, claim, resource, and mailbox counts."""
        return await mcp_status(
            identity=self.name,
            await_reply=self._await_reply,
            agent=self.agent,
        )

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

    async def directory(self) -> str:
        """Return the capability/resource discovery directory as JSON.

        Returns
        -------
        str
            The directory as indented JSON, or a no-response line when either
            required snapshot is missing.
        """
        manifest_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return the capability directory"
        state_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        if state_reply is None:
            return "the hub did not return the capability directory"
        snapshot = state_reply.get("snapshot", {})
        resources = snapshot.get("resources", []) if isinstance(snapshot, dict) else []
        manifest = manifest_reply.get("manifest", [])
        directory = build_capability_directory(
            manifest=manifest if isinstance(manifest, list) else [],
            resources=resources if isinstance(resources, list) else [],
        )
        return directory_to_json(directory)

    async def task_resource(self, task_id: str) -> str:
        """Return one board task through a dynamic MCP resource template.

        Parameters
        ----------
        task_id : str
            Board task id from ``synapse://task/{task_id}``.

        Returns
        -------
        str
            Task resource JSON, or a no-response line.
        """
        board_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        if board_reply is None:
            return "the hub did not return MCP task resource snapshots"
        board = board_reply.get("board", {})
        return task_resource_to_json(board if isinstance(board, dict) else {}, task_id)

    async def agent_resource(self, agent: str) -> str:
        """Return one agent's card and resources through an MCP resource template.

        Parameters
        ----------
        agent : str
            Agent identity from ``synapse://agent/{agent}``.

        Returns
        -------
        str
            Agent resource JSON, or a no-response line.
        """
        manifest_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return MCP agent resource snapshots"
        state_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        if state_reply is None:
            return "the hub did not return MCP agent resource snapshots"
        manifest = manifest_reply.get("manifest", [])
        snapshot = state_reply.get("snapshot", {})
        resources = snapshot.get("resources", []) if isinstance(snapshot, dict) else []
        return agent_resource_to_json(
            manifest if isinstance(manifest, list) else [],
            resources if isinstance(resources, list) else [],
            agent,
        )

    async def resource_kind_resource(self, kind: str) -> str:
        """Return resources of one kind through an MCP resource template.

        Parameters
        ----------
        kind : str
            Resource kind from ``synapse://resource-kind/{kind}``.

        Returns
        -------
        str
            Resource-kind JSON, or a no-response line.
        """
        state_reply = await self._await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        if state_reply is None:
            return "the hub did not return MCP resource-kind snapshots"
        snapshot = state_reply.get("snapshot", {})
        resources = snapshot.get("resources", []) if isinstance(snapshot, dict) else []
        return resource_kind_resource_to_json(
            resources if isinstance(resources, list) else [],
            kind,
        )

    async def route_task(
        self,
        task_id: str,
        limit: int = 5,
        include_zero: bool = False,
        event_store: str | None = None,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return advisory semantic route recommendations for a board task."""
        return await self.advisory_actions.route_task(
            task_id,
            limit=limit,
            include_zero=include_zero,
            event_store=event_store,
            event_store_key_file=event_store_key_file,
        )

    async def resource_bids(
        self,
        task_id: str,
        resource_kind: str | None = None,
        limit: int = 5,
        include_zero: bool = False,
    ) -> str:
        """Return advisory resource bids for a board task as JSON."""
        return await self.advisory_actions.resource_bids(
            task_id,
            resource_kind=resource_kind,
            limit=limit,
            include_zero=include_zero,
        )

    async def memory_recall(
        self,
        event_store: str,
        query: str,
        limit: int = 5,
        since_seq: int = 0,
        event_store_key_file: str | None = None,
    ) -> str:
        """Return deterministic local memory recall hits as JSON."""
        return await self.advisory_actions.memory_recall(
            event_store,
            query,
            limit=limit,
            since_seq=since_seq,
            event_store_key_file=event_store_key_file,
        )
