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

A ``group:<name>`` scope keeps the records of every namespace of one group the
dashboard declares in its owner-only groups file
(:mod:`synapse_channel.dashboard_scope_groups`); a namespace may be in two groups.

A ``peer:<hub id>`` scope is of another kind: it shows an observed peer hub's
folded state alone, marked advisory (:mod:`synapse_channel.dashboard_peer_scope`),
and no local record belongs to it.

The scope is applied before the fleet, risk and headline views are computed, so
every counter of a scoped document is a count in that scope. The ``scope`` block
states the hub-wide totals beside the scoped ones, and lists every namespace
present and every declared group with its counts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from synapse_channel.core.identity_namespace import project_of
from synapse_channel.dashboard_bounds import bound_snapshot
from synapse_channel.dashboard_peer_scope import peer_block, peer_snapshot
from synapse_channel.dashboard_scope_groups import SCOPE_GROUPS_FLAG, ScopeGroups

if TYPE_CHECKING:
    from pathlib import Path

    from synapse_channel.dashboard import DashboardSnapshot

ScopeKind = Literal["all", "none", "project", "group", "peer"]
SCOPE_FORMS = "all, none, project:<namespace>, group:<name> or peer:<hub id>"
"""The forms a ``scope`` query value may take, as a refusal names them."""

_MAX_NAMESPACE = 256


@dataclass(frozen=True, slots=True)
class DashboardScope:
    """One scope of the dashboard: the whole hub, one namespace, a declared group, none, or a peer.

    Parameters
    ----------
    kind : {"all", "none", "project", "group", "peer"}
        Which part of the fleet the scope keeps; ``peer`` shows an observed hub instead.
    namespace : str, optional
        The namespace of a ``project`` scope; empty for the other kinds.
    peer : str, optional
        The hub id of a ``peer`` scope; empty for the other kinds.
    group : str, optional
        The name of a ``group`` scope; empty for the other kinds.
    members : frozenset[str], optional
        The namespaces a ``group`` scope keeps; empty for the other kinds.
    declared : ScopeGroups or None, optional
        The groups the dashboard declares, which the ``scope`` block lists; not part
        of what the scope keeps, so two scopes compare by their kind and names alone.
    """

    kind: ScopeKind
    namespace: str = ""
    peer: str = ""
    group: str = ""
    members: frozenset[str] = frozenset()
    declared: ScopeGroups | None = field(default=None, compare=False)

    @property
    def text(self) -> str:
        """Return the scope as its query value, for example ``project:<ns>`` or ``peer:<id>``."""
        if self.kind == "project":
            return f"project:{self.namespace}"
        if self.kind == "group":
            return f"group:{self.group}"
        return f"peer:{self.peer}" if self.kind == "peer" else self.kind

    def holds(self, namespace: str) -> bool:
        """Return whether a local record of ``namespace`` (empty for none) belongs here."""
        if self.kind in ("all", "peer"):
            return self.kind == "all"
        if self.kind == "group":
            return namespace in self.members
        return namespace == self.namespace if self.kind == "project" else not namespace


WHOLE_HUB = DashboardScope("all")
"""The default scope: every record the hub sends, as before scopes existed."""


def _group_scope(name: str, groups: ScopeGroups | None) -> DashboardScope:
    """Return the scope of the declared group ``name``.

    Raises
    ------
    ValueError
        When the dashboard declares no groups (the message names the flag) or no
        group of that name.
    """
    if groups is None:
        raise ValueError(
            f"group scopes need a groups file: start the dashboard with {SCOPE_GROUPS_FLAG}"
        )
    members = groups.members(name)
    if members is None:
        raise ValueError("this dashboard declares no group of that name")
    return DashboardScope("group", group=name, members=members, declared=groups)


def parse_scope(values: list[str], groups: ScopeGroups | None = None) -> DashboardScope:
    """Return the scope one request asks for; no value asks for the whole hub.

    Parameters
    ----------
    values : list[str]
        Every ``scope`` value of the request's query.
    groups : ScopeGroups or None, optional
        The groups the dashboard declares (``--scope-groups``); ``None`` when it
        declares none, and then a ``group:`` scope is refused.

    Raises
    ------
    ValueError
        For more than one value, an unknown form, an empty, overlong, non-printable
        or slash-bearing name, or a group the dashboard does not declare; the
        message names the forms or the flag.
    """
    if not values:
        return WHOLE_HUB if groups is None else DashboardScope("all", declared=groups)
    if len(values) != 1:
        raise ValueError(f"scope accepts one value: {SCOPE_FORMS}")
    value = values[0]
    if value in ("all", "none"):
        return DashboardScope("all" if value == "all" else "none", declared=groups)
    kind, _, name = value.partition(":")
    if (
        kind not in ("project", "group", "peer")
        or not 0 < len(name) <= _MAX_NAMESPACE
        or "/" in name
        or not name.isprintable()
    ):
        raise ValueError(f"scope must be {SCOPE_FORMS}")
    if kind == "group":
        return _group_scope(name, groups)
    if kind == "project":
        return DashboardScope("project", namespace=name, declared=groups)
    return DashboardScope("peer", peer=name, declared=groups)


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


def _groups(
    declared: ScopeGroups | None, tasks: Iterable[Mapping[str, Any]], agents: Iterable[str]
) -> list[dict[str, Any]]:
    """Return every declared group with its namespaces and its task and agent counts."""
    if declared is None:
        return []
    task_namespaces = [_task_namespace(task) for task in tasks]
    agent_namespaces = [project_of(name) for name in agents]
    rows = []
    for name in declared.names():
        members = declared.members(name) or frozenset()
        rows.append(
            {
                "group": name,
                "namespaces": sorted(members),
                "tasks": sum(1 for namespace in task_namespaces if namespace in members),
                "agents": sum(1 for namespace in agent_namespaces if namespace in members),
            }
        )
    return rows


def scope_summary(
    whole: DashboardSnapshot, scoped: DashboardSnapshot, scope: DashboardScope
) -> dict[str, Any]:
    """Return the ``scope`` block: the scope, its counts beside the hub's, and what can be scoped.

    What can be scoped is every namespace present, every declared group and every
    observed peer.

    A peer scope's counts are the peer's, beside this hub's totals, and the block
    says ``advisory``.

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
        "advisory": scope.kind == "peer",
        "tasks_in_scope": len(_records(scoped.board.get("tasks"))),
        "tasks_total": len(all_tasks),
        "tasks_without_project": sum(1 for task in all_tasks if not _task_namespace(task)),
        "agents_in_scope": len(scoped.online_agents),
        "agents_total": len(whole.online_agents),
        "agents_without_namespace": sum(1 for name in whole.online_agents if not project_of(name)),
        "claims_in_scope": len(_records(scoped.state.get("active_claims"))),
        "claims_total": len(_records(whole.state.get("active_claims"))),
        "namespaces": _namespaces(all_tasks, whole.online_agents),
        "groups": _groups(scope.declared, all_tasks, whole.online_agents),
        "peers": [peer.hub_id for peer in whole.observed_peers],
    }


def scoped_document(
    snapshot: DashboardSnapshot,
    scope: DashboardScope,
    *,
    a2a_state_file: str | Path | None = None,
    closed: bool = False,
) -> dict[str, Any]:
    """Return the bounded document of ``snapshot`` in ``scope``, with its ``scope`` block.

    A peer scope's document is the observed peer's alone, marked ``advisory``, with a
    ``peer`` block that says whether the hub is observed and was reached.

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
    if scope.kind == "peer":
        # The local A2A bridge is not the peer's; its state stays out of a peer's document.
        scoped = peer_snapshot(snapshot, scope.peer)
        document = bound_snapshot(scoped.to_dict(), closed=closed)
        document["advisory"] = True
        document["peer"] = peer_block(snapshot, scope.peer)
    else:
        scoped = scope_snapshot(snapshot, scope)
        document = bound_snapshot(scoped.to_dict(a2a_state_file=a2a_state_file), closed=closed)
    document["scope"] = scope_summary(snapshot, scoped, scope)
    return document
