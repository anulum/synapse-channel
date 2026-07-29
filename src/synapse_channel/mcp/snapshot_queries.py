# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP snapshot queries and read-only resource projections
"""Translate MCP read queries into correlated hub snapshots."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from synapse_channel.core.capability_directory import build_capability_directory, directory_to_json
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.resource_views import (
    agent_resource_to_json,
    resource_kind_resource_to_json,
    task_resource_to_json,
)

Matcher = Callable[[dict[str, Any]], bool]
Sender = Callable[[], Awaitable[None]]
ReplyAwaiter = Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]


class _SnapshotAgent(Protocol):
    """Hub request surface needed by :class:`McpSnapshotQueries`."""

    async def request_board(self) -> None:
        """Request the shared board snapshot."""

    async def request_state(self) -> None:
        """Request the live coordination-state snapshot."""

    async def request_manifest(self) -> None:
        """Request the live capability-manifest snapshot."""


class McpSnapshotQueries:
    """Own MCP board, state, manifest, directory, and resource queries.

    Parameters
    ----------
    agent : _SnapshotAgent
        Connected hub client used to request read-only snapshots.
    await_reply : ReplyAwaiter
        Correlator owned by the bridge transport layer.
    """

    def __init__(self, agent: _SnapshotAgent, await_reply: ReplyAwaiter) -> None:
        self.agent = agent
        self.await_reply = await_reply

    @staticmethod
    def _render(reply: dict[str, Any] | None, key: str, on_timeout: str) -> str:
        """Return one reply field as stable indented JSON, or a timeout message."""
        if reply is None:
            return on_timeout
        return json.dumps(reply.get(key, {}), indent=2, sort_keys=True)

    async def board(self) -> str:
        """Return the shared task/progress blackboard as JSON."""
        reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        return self._render(reply, "board", "the hub did not return the board")

    async def state(self) -> str:
        """Return the live claims/checkpoints snapshot as JSON."""
        reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
            self.agent.request_state,
        )
        return self._render(reply, "snapshot", "the hub did not return its state")

    async def manifest(self) -> str:
        """Return the capability manifest of advertised agents as JSON."""
        reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        return self._render(reply, "manifest", "the hub did not return the manifest")

    async def directory(self) -> str:
        """Return the discovery-only capability and resource directory as JSON."""
        manifest_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return the capability directory"
        state_reply = await self.await_reply(
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
        """Return one board task through its dynamic MCP resource template."""
        board_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.BOARD_SNAPSHOT,
            self.agent.request_board,
        )
        if board_reply is None:
            return "the hub did not return MCP task resource snapshots"
        board = board_reply.get("board", {})
        return task_resource_to_json(board if isinstance(board, dict) else {}, task_id)

    async def agent_resource(self, agent: str) -> str:
        """Return one agent's card and resources through an MCP resource template."""
        manifest_reply = await self.await_reply(
            lambda data: data.get("type") == MessageType.MANIFEST_SNAPSHOT,
            self.agent.request_manifest,
        )
        if manifest_reply is None:
            return "the hub did not return MCP agent resource snapshots"
        state_reply = await self.await_reply(
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
        """Return resources of one kind through an MCP resource template."""
        state_reply = await self.await_reply(
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
