# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the dashboard's scope: one project's part of the fleet snapshot
"""Narrow the dashboard's fleet snapshot to one project, or to what has none.

One hub serves many projects. A scope is a lens on what this dashboard already
reads: it grants nothing and hides nothing the hub would otherwise send to this
principal. Membership has one rule per record and is never inferred:

* a task belongs to the project its ``project`` field names; a task without one
  belongs to the ``none`` scope, whoever created it;
* an online agent, its roles and its liveness belong to its name's namespace
  (the part before the first ``/``); a bare name belongs to ``none``;
* a claim belongs to its owner's namespace, and also to the scope of the board
  task it names, so a task in scope never looks unclaimed because an outside
  identity holds it;
* a progress note belongs to its task's scope, and so do the release receipts
  derived from it.

The scope is applied before the fleet, risk and headline views are computed, so
every counter of a scoped document is a count in that scope. The ``scope`` block
states the hub-wide totals beside the scoped ones, and lists every namespace
present with its counts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from synapse_channel.core.identity_namespace import project_of
from synapse_channel.dashboard_bounds import bound_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from synapse_channel.dashboard import DashboardSnapshot

ScopeKind = Literal["all", "none", "project"]
SCOPE_FORMS = "all, none or project:<namespace>"
"""The forms a ``scope`` query value may take, as a refusal names them."""

_MAX_NAMESPACE = 256


@dataclass(frozen=True, slots=True)
class DashboardScope:
    """One scope of the dashboard: the whole hub, one namespace, or no namespace.

    Parameters
    ----------
    kind : {"all", "none", "project"}
        Which part of the fleet the scope keeps.
    namespace : str, optional
        The namespace of a ``project`` scope; empty for the other kinds.
    """

    kind: ScopeKind
    namespace: str = ""

    @property
    def text(self) -> str:
        """Return the scope as its query value: ``all``, ``none`` or ``project:<ns>``."""
        return f"project:{self.namespace}" if self.kind == "project" else self.kind

    def holds(self, namespace: str) -> bool:
        """Return whether a record of ``namespace`` (empty for none) belongs here."""
        if self.kind == "all":
            return True
        return namespace == self.namespace if self.kind == "project" else not namespace


WHOLE_HUB = DashboardScope("all")
"""The default scope: every record the hub sends, as before scopes existed."""


def parse_scope(values: list[str]) -> DashboardScope:
    """Return the scope one request asks for; no value asks for the whole hub.

    Parameters
    ----------
    values : list[str]
        Every ``scope`` value of the request's query.

    Raises
    ------
    ValueError
        For more than one value, an unknown form, or an empty, overlong,
        non-printable or slash-bearing namespace; the message names the forms.
    """
    if not values:
        return WHOLE_HUB
    if len(values) != 1:
        raise ValueError(f"scope accepts one value: {SCOPE_FORMS}")
    value = values[0]
    if value in ("all", "none"):
        return DashboardScope("all" if value == "all" else "none")
    namespace = value.removeprefix("project:")
    if (
        namespace == value
        or not 0 < len(namespace) <= _MAX_NAMESPACE
        or "/" in namespace
        or not namespace.isprintable()
    ):
        raise ValueError(f"scope must be {SCOPE_FORMS}")
    return DashboardScope("project", namespace)


def _records(value: object) -> list[dict[str, Any]]:
    """Return the mapping items of a list; anything else is empty."""
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _task_namespace(task: Mapping[str, Any]) -> str:
    """Return the project a board task names, or empty when it names none."""
    return str(task.get("project") or "")


def _scoped_board(
    board: Mapping[str, Any], scope: DashboardScope
) -> tuple[dict[str, Any], set[str]]:
    """Return the board's tasks, ready ids and notes in ``scope``, and the kept task ids."""
    tasks = [task for task in _records(board.get("tasks")) if scope.holds(_task_namespace(task))]
    task_ids = {str(task.get("task_id")) for task in tasks}
    scoped = {
        **board,
        "tasks": tasks,
        "ready": [task_id for task_id in board.get("ready") or [] if str(task_id) in task_ids],
        "progress": [
            note for note in _records(board.get("progress")) if str(note.get("task_id")) in task_ids
        ],
    }
    return scoped, task_ids


def _scoped_state(
    state: Mapping[str, Any], scope: DashboardScope, task_ids: set[str]
) -> dict[str, Any]:
    """Return the state's claims (owner or task in scope) and seen agents in ``scope``."""
    return {
        **state,
        "active_claims": [
            claim
            for claim in _records(state.get("active_claims"))
            if scope.holds(project_of(str(claim.get("owner") or "")))
            or str(claim.get("task_id")) in task_ids
        ],
        "agents": [
            seen
            for seen in _records(state.get("agents"))
            if scope.holds(project_of(str(seen.get("agent") or "")))
        ],
    }


def scope_snapshot(snapshot: DashboardSnapshot, scope: DashboardScope) -> DashboardSnapshot:
    """Return the part of ``snapshot`` that belongs to ``scope``; the whole hub is returned as is.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        The whole snapshot fetched from the hub.
    scope : DashboardScope
        The scope to keep.
    """
    if scope.kind == "all":
        return snapshot
    board, task_ids = _scoped_board(snapshot.board, scope)
    agents = [name for name in snapshot.online_agents if scope.holds(project_of(name))]
    kept = set(agents)
    return replace(
        snapshot,
        online_agents=agents,
        state=_scoped_state(snapshot.state, scope, task_ids),
        board=board,
        agent_roles={name: roles for name, roles in snapshot.agent_roles.items() if name in kept},
        agent_liveness={
            name: value for name, value in snapshot.agent_liveness.items() if name in kept
        },
        wake_capabilities={
            name: value for name, value in snapshot.wake_capabilities.items() if name in kept
        },
    )


def _namespaces(tasks: Iterable[Mapping[str, Any]], agents: Iterable[str]) -> list[dict[str, Any]]:
    """Return every namespace present, with its task and agent counts, in name order."""
    task_counts = Counter(_task_namespace(task) for task in tasks)
    agent_counts = Counter(project_of(name) for name in agents)
    names = sorted((set(task_counts) | set(agent_counts)) - {""})
    return [
        {"namespace": name, "tasks": task_counts[name], "agents": agent_counts[name]}
        for name in names
    ]


def scope_summary(
    whole: DashboardSnapshot, scoped: DashboardSnapshot, scope: DashboardScope
) -> dict[str, Any]:
    """Return the ``scope`` block: the scope, its counts beside the hub's, and the namespaces.

    Parameters
    ----------
    whole : DashboardSnapshot
        The whole snapshot fetched from the hub.
    scoped : DashboardSnapshot
        The same snapshot narrowed to ``scope`` by :func:`scope_snapshot`.
    scope : DashboardScope
        The scope that was applied.
    """
    all_tasks = _records(whole.board.get("tasks"))
    return {
        "scope": scope.text,
        "applied": scope.kind != "all",
        "tasks_in_scope": len(_records(scoped.board.get("tasks"))),
        "tasks_total": len(all_tasks),
        "tasks_without_project": sum(1 for task in all_tasks if not _task_namespace(task)),
        "agents_in_scope": len(scoped.online_agents),
        "agents_total": len(whole.online_agents),
        "agents_without_namespace": sum(1 for name in whole.online_agents if not project_of(name)),
        "claims_in_scope": len(_records(scoped.state.get("active_claims"))),
        "claims_total": len(_records(whole.state.get("active_claims"))),
        "namespaces": _namespaces(all_tasks, whole.online_agents),
    }


def scoped_document(
    snapshot: DashboardSnapshot,
    scope: DashboardScope,
    *,
    a2a_state_file: str | Path | None = None,
    closed: bool = False,
) -> dict[str, Any]:
    """Return the bounded document of ``snapshot`` in ``scope``, with its ``scope`` block.

    ``/snapshot.json``, the live channel and the Studio projection all build their
    document here, so the same scope gives the same bytes on each.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        The whole snapshot fetched from the hub.
    scope : DashboardScope
        The scope to serve.
    a2a_state_file : str, pathlib.Path, or None, optional
        The A2A bridge state the fleet summary reads.
    closed : bool, optional
        Keep closed tasks as well (``?closed=1``).
    """
    scoped = scope_snapshot(snapshot, scope)
    document = bound_snapshot(scoped.to_dict(a2a_state_file=a2a_state_file), closed=closed)
    document["scope"] = scope_summary(snapshot, scoped, scope)
    return document
