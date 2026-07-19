# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wait-for graph and deadlock cycle detection
"""Wait-for graph: waiter→task edges with cycle detection over live ownership.

A wait edge maps a waiting agent to the *tasks* it waits for, not to the
agents currently holding them. Cycle detection projects those edges onto
live agent→agent edges by resolving each waited task's CURRENT holder from
the claims registry. That single indirection closes two defect classes:

* a claim, renewal, or handoff for task U can never erase a wait on task T,
  because edges are keyed by the waited tasks and only a satisfied wait (the
  waiter receiving that very task) is cleared; and
* a release, expiry, or handoff can never leave a stale agent edge that
  produces a false-positive deadlock refusal, because ownership is resolved
  live at cycle-check time — an edge whose task is not currently claimed has
  no holder and cannot close a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["prune_waits", "resolve_wait_edges", "would_create_cycle"]


def _owner_of(claim: Any) -> str:
    """Return the owner name of a claim object or claim mapping."""
    owner = getattr(claim, "owner", None)
    if owner:
        return str(owner)
    if isinstance(claim, Mapping):
        return str(claim.get("owner") or "")
    return ""


def resolve_wait_edges(
    waits: Mapping[str, set[str]], claims: Mapping[str, Any]
) -> dict[str, set[str]]:
    """Project waiter→task-set waits onto live waiter→holder-set agent edges.

    A wait edge resolves only while its task is currently claimed; an edge
    whose task is free contributes nothing to the agent graph and therefore
    cannot form a stale cycle.
    """
    edges: dict[str, set[str]] = {}
    for waiter, task_ids in waits.items():
        owners = {
            owner
            for task_id in task_ids
            if (claim := claims.get(task_id)) is not None
            for owner in [_owner_of(claim)]
            if owner
        }
        if owners:
            edges[waiter] = owners
    return edges


def prune_waits(waits: Mapping[str, set[str]], claims: Mapping[str, Any]) -> dict[str, set[str]]:
    """Drop wait edges whose task is not currently claimed.

    Hygiene companion of the live resolution in :func:`resolve_wait_edges`:
    call it after a release or expiry so the graph does not accumulate
    unresolvable edges. Waiters left with no tasks drop out entirely.
    """
    pruned: dict[str, set[str]] = {}
    for waiter, task_ids in waits.items():
        live = {task_id for task_id in task_ids if task_id in claims}
        if live:
            pruned[waiter] = live
    return pruned


def would_create_cycle(
    waits: Mapping[str, set[str]],
    claims: Mapping[str, Any],
    waiter: str,
    holder: str,
) -> bool:
    """Return whether ``waiter`` waiting for ``holder`` would close a cycle.

    Parameters
    ----------
    waits : Mapping[str, set[str]]
        The current wait-for graph mapping each waiting agent to the tasks it
        waits for.
    claims : Mapping[str, Any]
        The live claims registry (task id → claim object or mapping with an
        ``owner``), used to resolve each waited task's current holder.
    waiter : str
        The agent that wants to start waiting.
    holder : str
        The agent currently holding what ``waiter`` wants.

    Returns
    -------
    bool
        ``True`` if adding the edge ``waiter -> holder`` would create a cycle
        (including the degenerate self-wait ``waiter == holder``); ``False``
        when the wait is safe to register.
    """
    if waiter == holder:
        return True
    edges = resolve_wait_edges(waits, claims)
    stack = [holder]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == waiter:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False
