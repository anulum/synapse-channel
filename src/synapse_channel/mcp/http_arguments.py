# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP argument authority
"""Refuse cross-project references before dispatching existing business actions."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from synapse_channel.mcp.bridge import SynapseHubBridge

_BOARD = TypeAdapter(dict[str, Any])
_TASKS = TypeAdapter(list[dict[str, Any]])


class HttpArgumentPolicy:
    """Bind remote task and recipient arguments to a provisioned project.

    Parameters
    ----------
    bridge : SynapseHubBridge
        Real hub connection used for the current task ownership snapshot.
    project : str
        Operator-selected project namespace.
    """

    def __init__(self, bridge: SynapseHubBridge, project: str) -> None:
        self.bridge = bridge
        self.project = project
        self.prefix = project + "/"

    def recipient(self, value: object) -> None:
        """Require an exact project identity, refusing broadcast and glob aliases."""
        if (
            not isinstance(value, str)
            or not value.startswith(self.prefix)
            or len(value) <= len(self.prefix)
            or len(value) > 256
            or any(character in value for character in "*?[],")
            or any(character.isspace() for character in value)
            or value.strip() != value
        ):
            raise PermissionError("remote MCP operation denied")

    async def authorize(self, name: str, arguments: dict[str, Any]) -> None:
        """Check every task reference against the actual hub board.

        Parameters
        ----------
        name : str
            Registered mutation name.
        arguments : dict[str, Any]
            Client arguments, validated again by the registered tool schema.

        Raises
        ------
        PermissionError
            On foreign, missing, malformed or ambiguous authority.
        """
        if name == "synapse_send":
            self.recipient(arguments.get("target"))
            return
        task = arguments.get("task_id")
        if (
            not isinstance(task, str)
            or not task.startswith(self.prefix)
            or len(task) <= len(self.prefix)
            or len(task) > 256
            or task.strip() != task
        ):
            raise PermissionError("remote MCP operation denied")
        if name == "synapse_claim" and arguments.get("paths"):
            raise PermissionError("remote MCP file claims require local workspace authority")
        if name == "synapse_release" and arguments.get("changed_files"):
            raise PermissionError("remote MCP file receipts require local workspace authority")
        if name == "synapse_handoff":
            self.recipient(arguments.get("to_agent"))
        if arguments.get("suggested_owner"):
            self.recipient(arguments["suggested_owner"])
        board = _BOARD.validate_json(await self.bridge.board(), strict=True)
        rows = _TASKS.validate_python(board.get("tasks"), strict=True)
        tasks = {row["task_id"]: row for row in rows if isinstance(row.get("task_id"), str)}
        current = tasks.get(task)
        if current is not None:
            self._task(current)
        elif name != "synapse_task_declare":
            raise PermissionError("remote MCP operation denied")
        dependencies = arguments.get("depends_on") or []
        if not isinstance(dependencies, list) or len(dependencies) > 128:
            raise PermissionError("remote MCP operation denied")
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in tasks:
                raise PermissionError("remote MCP operation denied")
            self._task(tasks[dependency])

    def _task(self, row: dict[str, Any]) -> None:
        """Require explicit project and hub-bound creator, rather than an ID alone."""
        creator = row.get("created_by")
        if (
            row.get("project") != self.project
            or not isinstance(creator, str)
            or not creator.startswith(self.prefix)
        ):
            raise PermissionError("remote MCP operation denied")
