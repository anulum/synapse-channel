# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — project-scoped remote MCP snapshots
"""Project existing hub snapshots before exposing them to remote MCP clients."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import TypeAdapter

from synapse_channel.core.capability_directory import build_capability_directory, directory_to_json
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.resource_views import (
    agent_resource_to_json,
    resource_kind_resource_to_json,
    task_resource_to_json,
)
from synapse_channel.waiter_identity import split_roster

Result = TypeVar("Result")
OBJECT_SNAPSHOT = TypeAdapter(dict[str, object])
ROW_SNAPSHOT = TypeAdapter(list[dict[str, object]])


def _validate(value: object, adapter: TypeAdapter[Result], *, json_input: bool = False) -> Result:
    """Validate an upstream shape and omit its contents from failure diagnostics."""
    try:
        if json_input:
            return adapter.validate_json(str(value), strict=True)
        return adapter.validate_python(value, strict=True)
    except ValueError:
        raise RuntimeError("remote MCP hub snapshot unavailable") from None


def _json(value: object) -> str:
    """Render a detached projection in the existing MCP JSON format."""
    return json.dumps(value, sort_keys=True, indent=2)


class ProjectMcpViews:
    """Expose project-owned data through the existing hub bridge.

    Parameters
    ----------
    bridge : SynapseHubBridge
        Connected bridge whose identity belongs to the selected project.
    project : str
        Operator-granted project, never selected from tool arguments.

    Notes
    -----
    A task needs both explicit hub project scope and a project-qualified creator.
    Unscoped tasks and claims on unseen tasks remain private. Hub snapshot caps
    still apply; these projections never infer a project's total from global counts.
    """

    def __init__(self, bridge: SynapseHubBridge, project: str) -> None:
        if not project or "/" in project or not bridge.name.startswith(project + "/"):
            raise ValueError("remote MCP bridge is outside its granted project")
        self.bridge = bridge
        self.project = project

    def _agent(self, value: object) -> bool:
        """Check the hub-bound identity namespace, rather than advertised role aliases."""
        return isinstance(value, str) and value.startswith(self.project + "/")

    async def _board(self) -> dict[str, object]:
        """Load a board and remove foreign references before resource rendering."""
        source = _validate(await self.bridge.board(), OBJECT_SNAPSHOT, json_input=True)
        tasks = [
            row
            for row in _validate(source.get("tasks"), ROW_SNAPSHOT)
            if row.get("project") == self.project and self._agent(row.get("created_by"))
        ]
        identifiers = {row["task_id"] for row in tasks if isinstance(row.get("task_id"), str)}
        for row in tasks:
            dependencies = row.get("depends_on")
            row["depends_on"] = (
                [item for item in dependencies if isinstance(item, str) and item in identifiers]
                if isinstance(dependencies, list)
                else []
            )
            if not self._agent(row.get("suggested_owner")):
                row["suggested_owner"] = ""
        ready = source.get("ready")
        progress = [
            row
            for row in _validate(source.get("progress"), ROW_SNAPSHOT)
            if row.get("task_id") in identifiers and self._agent(row.get("author"))
        ]
        return {
            "tasks": tasks,
            "progress": progress,
            "ready": [item for item in ready if isinstance(item, str) and item in identifiers]
            if isinstance(ready, list)
            else [],
            "scope": self.project,
            "source_may_be_bounded": True,
        }

    async def board(self) -> str:
        """Return scoped tasks, readiness and progress without global counts."""
        return _json(await self._board())

    async def _state(self) -> dict[str, object]:
        """Project claims using a fresh board, and filter bound agent identities."""
        board = await self._board()
        identifiers = {row["task_id"] for row in _validate(board["tasks"], ROW_SNAPSHOT)}
        source = _validate(await self.bridge.state(), OBJECT_SNAPSHOT, json_input=True)
        return {
            "active_claims": [
                row
                for row in _validate(source.get("active_claims"), ROW_SNAPSHOT)
                if row.get("task_id") in identifiers and self._agent(row.get("owner"))
            ],
            "agents": [
                row
                for row in _validate(source.get("agents"), ROW_SNAPSHOT)
                if self._agent(row.get("agent"))
            ],
            "resources": [
                row
                for row in _validate(source.get("resources"), ROW_SNAPSHOT)
                if self._agent(row.get("agent"))
            ],
            "scope": self.project,
            "source_may_be_bounded": True,
        }

    async def state(self) -> str:
        """Return scoped claims, agents and resources, omitting global operator queues."""
        return _json(await self._state())

    async def _manifest(self) -> list[dict[str, object]]:
        """Filter capability cards by the advertising hub-bound identity."""
        source = _validate(await self.bridge.manifest(), ROW_SNAPSHOT, json_input=True)
        return [row for row in source if self._agent(row.get("agent"))]

    async def manifest(self) -> str:
        """Return only capability cards advertised by this project's identities."""
        return _json(await self._manifest())

    async def directory(self) -> str:
        """Build the existing discovery directory from scoped cards and offers."""
        manifest, state = await self._manifest(), await self._state()
        resources = _validate(state["resources"], ROW_SNAPSHOT)
        return directory_to_json(build_capability_directory(manifest=manifest, resources=resources))

    async def status(self) -> str:
        """Return project counts and this identity's mailbox and waiter status."""
        state = await self._state()
        own = _validate(await self.bridge.status(), OBJECT_SNAPSHOT, json_input=True)
        names = [str(row["agent"]) for row in _validate(state["agents"], ROW_SNAPSHOT)]
        agents, waiters = split_roster(names)
        return _json(
            {
                "identity": self.bridge.name,
                "scope": self.project,
                "active_claims": len(_validate(state["active_claims"], ROW_SNAPSHOT)),
                "resources": len(_validate(state["resources"], ROW_SNAPSHOT)),
                "online_agents": len(agents),
                "waiters": len(waiters),
                "waiter_online": own.get("waiter_online"),
                "mailbox_pending": own.get("mailbox_pending"),
                "mailbox_pending_available": own.get("mailbox_pending_available"),
            }
        )

    async def task_resource(self, task_id: str) -> str:
        """Render an existing task resource from a project-scoped board."""
        return task_resource_to_json(await self._board(), task_id)

    async def agent_resource(self, agent: str) -> str:
        """Render an agent resource from scoped capabilities and resource offers."""
        if not self._agent(agent):
            raise PermissionError("remote MCP resource denied")
        manifest, state = await self._manifest(), await self._state()
        resources = _validate(state["resources"], ROW_SNAPSHOT)
        return agent_resource_to_json(manifest, resources, agent)

    async def resource_kind_resource(self, kind: str) -> str:
        """Render a resource-kind view without foreign project offers."""
        resources = _validate((await self._state())["resources"], ROW_SNAPSHOT)
        return resource_kind_resource_to_json(resources, kind)
