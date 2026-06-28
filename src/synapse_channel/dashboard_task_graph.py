# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard task dependency graph
"""Read-only task dependency graph projection for dashboard snapshots."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES

JsonDict = dict[str, Any]
"""JSON-compatible mapping returned by dashboard graph projections."""


@dataclass(frozen=True)
class TaskGraphNode:
    """One blackboard task rendered as a dependency graph node.

    Attributes
    ----------
    task_id : str
        Stable blackboard task identifier.
    title : str
        Human-readable task title.
    status : str
        Current blackboard task status.
    ready : bool
        Whether the hub reports the task in the current ready set.
    """

    task_id: str
    title: str
    status: str
    ready: bool

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class TaskGraphEdge:
    """One prerequisite edge between blackboard tasks.

    Attributes
    ----------
    dependency : str
        Prerequisite task identifier.
    task_id : str
        Dependent task identifier.
    satisfied : bool
        Whether the prerequisite has reached a terminal status.
    missing : bool
        Whether the prerequisite is absent from the current board snapshot.
    from_status : str
        Prerequisite status or ``missing`` when absent.
    """

    dependency: str
    task_id: str
    satisfied: bool
    missing: bool
    from_status: str

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "from": self.dependency,
            "to": self.task_id,
            "satisfied": self.satisfied,
            "missing": self.missing,
            "from_status": self.from_status,
        }


@dataclass(frozen=True)
class TaskDependencyGraph:
    """Derived blackboard task dependency graph.

    Attributes
    ----------
    nodes : list[TaskGraphNode]
        Known board tasks in deterministic order.
    edges : list[TaskGraphEdge]
        Dependency edges from prerequisite to dependent task.
    blocked : list[dict[str, Any]]
        Blocked task ids and the dependency ids that currently block them.
    ready : list[str]
        Ready task ids reported by the board snapshot.
    """

    nodes: list[TaskGraphNode]
    edges: list[TaskGraphEdge]
    blocked: list[JsonDict]
    ready: list[str]

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "blocked": self.blocked,
            "ready": self.ready,
            "total_tasks": len(self.nodes),
            "total_edges": len(self.edges),
        }


def _as_mappings(value: object) -> list[Mapping[str, object]]:
    """Return mapping items from an arbitrary JSON list value."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _as_strings(value: object) -> list[str]:
    """Return stringified values from an arbitrary JSON list value."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _sort_unique(values: Sequence[str]) -> list[str]:
    """Return non-empty unique strings in deterministic order."""
    return sorted({value for value in values if value})


def _task_index(tasks: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    """Return a task-id keyed index for board task mappings."""
    return {str(task.get("task_id", "")): task for task in tasks if str(task.get("task_id", ""))}


def _dependency_status(
    dependency: str,
    tasks_by_id: Mapping[str, Mapping[str, object]],
) -> tuple[str, bool, bool]:
    """Return dependency status, satisfaction flag, and missing flag."""
    task = tasks_by_id.get(dependency)
    if task is None:
        return "missing", False, True
    status = str(task.get("status", ""))
    return status, status in TERMINAL_LEDGER_STATUSES, False


def _blocked_rows(
    tasks: Sequence[Mapping[str, object]],
    tasks_by_id: Mapping[str, Mapping[str, object]],
) -> list[JsonDict]:
    """Return blocked task rows with unmet dependency ids."""
    rows: list[JsonDict] = []
    for task in tasks:
        if str(task.get("status", "")) != "blocked":
            continue
        blocked_by: list[str] = []
        for dependency in _as_strings(task.get("depends_on")):
            _status, satisfied, _missing = _dependency_status(dependency, tasks_by_id)
            if not satisfied:
                blocked_by.append(dependency)
        rows.append({"task_id": str(task.get("task_id", "")), "blocked_by": blocked_by})
    rows.sort(key=lambda row: str(row["task_id"]))
    return rows


def build_task_dependency_graph(board: Mapping[str, object]) -> TaskDependencyGraph:
    """Build a deterministic dependency graph from a board snapshot.

    Parameters
    ----------
    board : Mapping[str, object]
        Blackboard snapshot returned by the hub.

    Returns
    -------
    TaskDependencyGraph
        Read-only graph projection with known task nodes, prerequisite edges,
        blocked rows, and the board's ready set.
    """
    tasks = _as_mappings(board.get("tasks"))
    tasks_by_id = _task_index(tasks)
    ready = _sort_unique(_as_strings(board.get("ready")))
    ready_set = set(ready)
    nodes = [
        TaskGraphNode(
            task_id=task_id,
            title=str(task.get("title", "")),
            status=str(task.get("status", "")),
            ready=task_id in ready_set,
        )
        for task_id, task in sorted(tasks_by_id.items())
    ]
    edges: list[TaskGraphEdge] = []
    for task in tasks:
        task_id = str(task.get("task_id", ""))
        for dependency in _as_strings(task.get("depends_on")):
            status, satisfied, missing = _dependency_status(dependency, tasks_by_id)
            edges.append(
                TaskGraphEdge(
                    dependency=dependency,
                    task_id=task_id,
                    satisfied=satisfied,
                    missing=missing,
                    from_status=status,
                )
            )
    return TaskDependencyGraph(
        nodes=nodes,
        edges=edges,
        blocked=_blocked_rows(tasks, tasks_by_id),
        ready=ready,
    )


def _escape(value: object) -> str:
    """Return ``value`` escaped for HTML text nodes."""
    return html.escape(str(value), quote=True)


def _render_nodes(nodes: Sequence[TaskGraphNode]) -> str:
    """Render task graph nodes as compact HTML list items."""
    if not nodes:
        return '<li class="muted">No task dependencies</li>'
    rows: list[str] = []
    for node in nodes:
        ready = " ready" if node.ready else ""
        rows.append(
            "<li>"
            f"<strong>{_escape(node.task_id)}</strong> "
            f"<span>{_escape(node.status)}{ready}</span><br>"
            f"<small>{_escape(node.title)}</small>"
            "</li>"
        )
    return "".join(rows)


def _render_edges(edges: Sequence[TaskGraphEdge]) -> str:
    """Render task graph edges as compact HTML list items."""
    if not edges:
        return '<li class="muted">No task dependencies</li>'
    rows: list[str] = []
    for edge in edges:
        state = "satisfied" if edge.satisfied else "blocked"
        if edge.missing:
            state = "missing"
        rows.append(
            "<li>"
            f"<strong>{_escape(edge.dependency)}</strong> -> "
            f"<strong>{_escape(edge.task_id)}</strong> "
            f"<small>{_escape(state)}; prerequisite status: {_escape(edge.from_status)}</small>"
            "</li>"
        )
    return "".join(rows)


def render_task_dependency_graph_html(board: Mapping[str, object]) -> str:
    """Render task dependency graph sections for the dashboard.

    Parameters
    ----------
    board : Mapping[str, object]
        Blackboard snapshot returned by the hub.

    Returns
    -------
    str
        Escaped HTML sections for embedding in the read-only dashboard page.
    """
    graph = build_task_dependency_graph(board)
    return f"""
    <section>
      <h2>Task dependency graph</h2>
      <ul>{_render_nodes(graph.nodes)}</ul>
    </section>
    <section>
      <h2>Task dependency edges</h2>
      <ul>{_render_edges(graph.edges)}</ul>
    </section>
"""
