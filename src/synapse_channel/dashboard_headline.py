# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the dashboard's headline counters, computed once
"""Compute every headline counter once, on the whole board, with one definition each.

The cockpit, Studio, the classic page and the Go client all read this block;
none of them counts again. Each name below has one meaning (the counter
glossary in ``docs/cockpit.md`` repeats them):

``agents_live``
    Identities connected now, not counting waiter sidecars or presence holders.
``waiters_missing``
    Live identities with no waiter sidecar.
``claims_active`` / ``claims_stale``
    Claims whose lease expires after the snapshot's time / at or before it.
``tasks_open`` / ``tasks_closed``
    Tasks whose status is not ``done`` or ``cancelled`` / is one of them.
``tasks_in_progress``
    Tasks whose status is ``in_progress``.
``tasks_ready``
    The hub's ready list: status ``open`` and every prerequisite closed.
``tasks_blocked``
    Open tasks that cannot start: ``tasks_blocked_marked`` (status
    ``blocked``) plus ``tasks_blocked_waiting`` (status ``open``, but a
    prerequisite is not closed or is not on the board). A task in progress has
    started, so an unfinished prerequisite does not count it as blocked.
``risk_signals`` / ``risk_signals_red`` / ``risk_signals_amber``
    Every risk signal / the red ones (an unsafe condition) / the amber ones
    (impeded progress).
``branch_conflicts``
    Pairs of live git-scoped claims on different branches towards one base
    whose paths overlap.
``peers_reachable`` / ``peers_total``
    Observed peer hubs that answered / that are configured.
``seats_listening`` / ``seats_active_not_listening`` / ``seats_silent``
    Seats in each state of :mod:`synapse_channel.dashboard_seats`.
``seats_deaf``
    Seats the hub judges not proven live (no live waiter, no reaction within its
    window): the seats a directed message to which draws a warning to its sender.
``presence_holders``
    Connected names that declared the wake capability ``passive`` and are not
    waiter sidecars (``synapse listen``, the ``synapse-presence@`` unit). They
    receive and never act, so they are neither agents nor seats.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES

HEADLINE_KEYS = (
    "agents_live",
    "waiters_missing",
    "claims_active",
    "claims_stale",
    "tasks_open",
    "tasks_in_progress",
    "tasks_ready",
    "tasks_blocked",
    "tasks_blocked_marked",
    "tasks_blocked_waiting",
    "tasks_closed",
    "risk_signals",
    "risk_signals_red",
    "risk_signals_amber",
    "branch_conflicts",
    "peers_reachable",
    "peers_total",
    "seats_listening",
    "seats_active_not_listening",
    "seats_silent",
    "seats_deaf",
    "presence_holders",
)
"""Every headline counter, in display order."""


def _mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[Mapping[str, Any]]:
    """Return the mapping items of a list; anything else is empty."""
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _count(value: object) -> int:
    """Return a non-boolean integer as given, the length of a list, else zero."""
    if isinstance(value, list):
        return len(value)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _seat_counts(seats: list[Mapping[str, Any]]) -> dict[str, int]:
    """Count the seat projection's rows by state."""
    states = [seat.get("state") for seat in seats]
    return {
        "seats_listening": states.count("listening"),
        "seats_active_not_listening": states.count("active, not listening"),
        "seats_silent": states.count("silent"),
        "seats_deaf": sum(
            1 for seat in seats if _mapping(seat.get("reaction")).get("proven") is False
        ),
    }


def _task_counts(board: Mapping[str, Any], graph: Mapping[str, Any]) -> dict[str, int]:
    """Count the board's tasks by the headline's task definitions."""
    tasks = _records(board.get("tasks"))
    status = {str(task.get("task_id")): str(task.get("status")) for task in tasks}
    open_ids = {
        task_id for task_id, value in status.items() if value not in TERMINAL_LEDGER_STATUSES
    }
    marked = {task_id for task_id in open_ids if status[task_id] == "blocked"}
    waiting = {
        str(edge.get("to"))
        for edge in _records(graph.get("edges"))
        if edge.get("satisfied") is not True and status.get(str(edge.get("to"))) == "open"
    }
    return {
        "tasks_open": len(open_ids),
        "tasks_in_progress": sum(1 for value in status.values() if value == "in_progress"),
        "tasks_blocked": len(marked) + len(waiting),
        "tasks_blocked_marked": len(marked),
        "tasks_blocked_waiting": len(waiting),
        "tasks_closed": len(status) - len(open_ids),
    }


def build_headline(
    board: Mapping[str, Any],
    fleet: Mapping[str, Any],
    risk: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> dict[str, int]:
    """Return every headline counter, keyed as :data:`HEADLINE_KEYS`.

    Parameters
    ----------
    board : Mapping[str, Any]
        The whole board (before any bound).
    fleet : Mapping[str, Any]
        The derived ``fleet`` section of the same snapshot.
    risk : Mapping[str, Any]
        The derived ``risk`` section of the same snapshot.
    observed : Mapping[str, Any]
        The observed-peer health section.

    Returns
    -------
    dict[str, int]
        One count per name; a missing or malformed source counts zero.
    """
    agents, claims = _mapping(fleet.get("agents")), _mapping(fleet.get("claims"))
    signals = _records(risk.get("signals"))
    counts = {
        "agents_live": _count(agents.get("live")),
        "waiters_missing": _count(agents.get("missing_waiters")),
        "presence_holders": _count(agents.get("presence_holders")),
        "claims_active": _count(claims.get("active")),
        "claims_stale": _count(claims.get("stale")),
        "tasks_ready": _count(_mapping(fleet.get("tasks")).get("ready")),
        "risk_signals": len(signals),
        "risk_signals_red": sum(1 for signal in signals if signal.get("level") == "red"),
        "risk_signals_amber": sum(1 for signal in signals if signal.get("level") == "amber"),
        "branch_conflicts": _count(fleet.get("branch_conflicts")),
        "peers_reachable": _count(observed.get("peers_reachable")),
        "peers_total": _count(observed.get("peers_total")),
        **_seat_counts(_records(fleet.get("seats"))),
        **_task_counts(board, _mapping(fleet.get("task_graph"))),
    }
    return {key: counts[key] for key in HEADLINE_KEYS}
