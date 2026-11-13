# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the bounded fleet snapshot the dashboard serves
"""Bound the dashboard's fleet snapshot without losing an exact count.

On the ecosystem's own hub the full snapshot measured 9,061,113 bytes on
2026-11-13: the board's progress ledger was 4,539,408 bytes (5,000 notes),
the release receipts derived from it 3,938,658, and the 415 closed tasks
152,134. A poll every two seconds downloaded all of it. The bounded document
keeps what the surfaces show and says exactly what it left out:

* every task that is not closed, in full; closed tasks only when asked for;
* the newest :data:`NEWEST_PROGRESS_NOTES` progress notes and the newest
  :data:`NEWEST_RECEIPTS` receipts;
* the task graph's nodes of the tasks in the document and every edge that
  touches one (an edge keeps its prerequisite's status);
* a ``bounds`` block with the exact totals, counted from the full document
  before anything is cut.

``fleet`` and ``risk`` are computed from the full board before this cut, so
their counts and signals do not change. The full progress ledger stays
reachable through the ``/events.json`` feed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES

NEWEST_PROGRESS_NOTES = 200
"""Progress notes kept: the cockpit's spine and panels read only recent notes."""

NEWEST_RECEIPTS = 50
"""Receipts kept: the classic page lists ten, the posture counts the total."""


def _records(value: object) -> list[dict[str, Any]]:
    """Return copies of the mapping items of a list; anything else is empty."""
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _bounded_graph(graph: object, kept: set[str]) -> tuple[dict[str, Any], int]:
    """Return the nodes of ``kept`` and every edge touching one, and the full node count.

    A node stands for a task and the cockpit draws one board row per node, so a
    task left out of the document leaves out its node too. An edge carries its
    prerequisite's status and whether it is satisfied, so a dependency on a
    closed task still reads correctly without that task's node.
    """
    source = graph if isinstance(graph, Mapping) else {}
    nodes, edges = _records(source.get("nodes")), _records(source.get("edges"))
    return (
        {
            **source,
            "nodes": [node for node in nodes if node.get("task_id") in kept],
            "edges": [edge for edge in edges if edge.get("from") in kept or edge.get("to") in kept],
        },
        len(nodes),
    )


def bound_snapshot(document: Mapping[str, Any], *, closed: bool = False) -> dict[str, Any]:
    """Return the bounded form of a full ``DashboardSnapshot.to_dict()`` document.

    Parameters
    ----------
    document : Mapping[str, Any]
        The full snapshot document.
    closed : bool, optional
        Keep closed tasks as well (``/snapshot.json?closed=1``).

    Returns
    -------
    dict[str, Any]
        A new document; ``document`` itself is not changed.
    """
    bounded = dict(document)
    board = dict(document.get("board") or {})
    fleet = dict(document.get("fleet") or {})
    tasks = _records(board.get("tasks"))
    open_tasks = [task for task in tasks if task.get("status") not in TERMINAL_LEDGER_STATUSES]
    sent = tasks if closed else open_tasks
    progress, receipts = _records(board.get("progress")), _records(fleet.get("receipts"))
    board["tasks"] = sent
    board["progress"] = progress[-NEWEST_PROGRESS_NOTES:]
    fleet["receipts"] = receipts[-NEWEST_RECEIPTS:]
    kept_ids = {str(task.get("task_id")) for task in sent}
    graph, graph_total = _bounded_graph(fleet.get("task_graph"), kept_ids)
    fleet["task_graph"] = graph
    bounded["board"], bounded["fleet"] = board, fleet
    bounded["bounds"] = {
        "closed_included": closed,
        "tasks_total": len(tasks),
        "tasks_by_status": dict(sorted(Counter(str(task.get("status")) for task in tasks).items())),
        "tasks_sent": len(sent),
        "closed_omitted": len(tasks) - len(sent),
        "progress_total": len(progress),
        "progress_sent": len(board["progress"]),
        "receipts_total": len(receipts),
        "receipts_sent": len(fleet["receipts"]),
        "graph_nodes_total": graph_total,
        "graph_nodes_sent": len(graph["nodes"]),
    }
    return bounded
