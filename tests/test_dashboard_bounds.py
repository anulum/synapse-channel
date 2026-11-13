# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the bounded fleet snapshot over a long-running board
"""Bound a seeded 1,000-task, 5,000-note fleet and keep every count exact."""

from __future__ import annotations

import copy
import json
from typing import Any

from dashboard_bounds_helpers import seeded_snapshot
from synapse_channel.dashboard_bounds import (
    NEWEST_PROGRESS_NOTES,
    NEWEST_RECEIPTS,
    bound_snapshot,
)

BOUNDED_BYTE_BUDGET = 262_144
"""The bounded document of the seeded fleet measured 159,174 bytes on 2026-11-13."""

TENFOLD_CLOSED = (
    ("done", 6000),
    ("cancelled", 500),
    ("open", 250),
    ("in_progress", 50),
    ("blocked", 50),
)


def _size(document: dict[str, Any]) -> int:
    return len(json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def test_open_work_travels_whole_and_every_count_stays_exact() -> None:
    full = seeded_snapshot().to_dict()
    pristine = copy.deepcopy(full)
    bounded = bound_snapshot(full)

    assert full == pristine
    assert bounded["bounds"] == {
        "closed_included": False,
        "tasks_total": 1000,
        "tasks_by_status": {
            "blocked": 50,
            "cancelled": 50,
            "done": 600,
            "in_progress": 50,
            "open": 250,
        },
        "tasks_sent": 350,
        "closed_omitted": 650,
        "progress_total": 5000,
        "progress_sent": NEWEST_PROGRESS_NOTES,
        "receipts_total": 500,
        "receipts_sent": NEWEST_RECEIPTS,
        "graph_nodes_total": 1000,
        "graph_nodes_sent": 350,
    }
    open_rows = [
        row for row in full["board"]["tasks"] if row["status"] not in {"done", "cancelled"}
    ]
    assert bounded["board"]["tasks"] == open_rows
    assert bounded["board"]["progress"] == full["board"]["progress"][-NEWEST_PROGRESS_NOTES:]
    assert bounded["fleet"]["receipts"] == full["fleet"]["receipts"][-NEWEST_RECEIPTS:]
    for section in ("tasks", "agents", "claims", "a2a"):
        assert bounded["fleet"][section] == full["fleet"][section]
    assert bounded["risk"] == full["risk"]
    assert bounded["hub_id"] == "hub-seed"


def test_the_graph_keeps_open_nodes_and_every_edge_that_touches_one() -> None:
    """A closed task has no node (the cockpit draws a row per node); its edges remain."""
    full = seeded_snapshot().to_dict()
    graph = bound_snapshot(full)["fleet"]["task_graph"]
    node_ids = {node["task_id"] for node in graph["nodes"]}

    assert node_ids == {f"T{number:04d}" for number in range(650, 1000)}
    assert graph["edges"] == full["fleet"]["task_graph"]["edges"]
    assert {edge["from"] for edge in graph["edges"]} == {f"T{n:04d}" for n in range(50)}
    assert all(edge["satisfied"] and edge["from_status"] == "done" for edge in graph["edges"])


def test_an_edge_between_two_closed_tasks_leaves_with_them() -> None:
    bounded = bound_snapshot(
        {
            "board": {
                "tasks": [
                    {"task_id": "A", "status": "done"},
                    {"task_id": "B", "status": "cancelled"},
                    {"task_id": "C", "status": "open"},
                ]
            },
            "fleet": {
                "task_graph": {
                    "nodes": [{"task_id": "A"}, {"task_id": "B"}, {"task_id": "C"}],
                    "edges": [{"from": "A", "to": "B"}, {"from": "C", "to": "A"}],
                }
            },
        }
    )

    assert bounded["fleet"]["task_graph"] == {
        "nodes": [{"task_id": "C"}],
        "edges": [{"from": "C", "to": "A"}],
    }
    assert (bounded["bounds"]["graph_nodes_total"], bounded["bounds"]["graph_nodes_sent"]) == (3, 1)


def test_closed_tasks_come_only_when_asked_for() -> None:
    full = seeded_snapshot().to_dict()
    everything = bound_snapshot(full, closed=True)

    assert everything["board"]["tasks"] == full["board"]["tasks"]
    assert everything["bounds"]["closed_included"] is True
    assert everything["bounds"]["closed_omitted"] == 0
    assert everything["bounds"]["tasks_sent"] == 1000
    assert len(everything["fleet"]["task_graph"]["nodes"]) == 1000
    assert len(everything["board"]["progress"]) == NEWEST_PROGRESS_NOTES


def test_the_document_does_not_grow_with_closed_tasks() -> None:
    small_full = seeded_snapshot().to_dict()
    large_full = seeded_snapshot(plan=TENFOLD_CLOSED).to_dict()
    small, large = bound_snapshot(small_full), bound_snapshot(large_full)

    assert large["bounds"]["closed_omitted"] == 6500
    assert _size(large_full) - _size(small_full) > 1_000_000
    # Only longer ids and wider counts differ: "T6500" against "T0650", 6000 against 600.
    assert _size(large) - _size(small) < 1024
    assert _size(small) <= BOUNDED_BYTE_BUDGET
    assert _size(large) <= BOUNDED_BYTE_BUDGET


def test_a_board_without_lists_or_graph_bounds_to_empty_parts() -> None:
    bounded = bound_snapshot(
        {
            "board": {"tasks": "not a list", "progress": [None, 3]},
            "fleet": {"receipts": None, "task_graph": "not a graph"},
        }
    )

    assert bounded["board"] == {"tasks": [], "progress": []}
    assert bounded["fleet"] == {"receipts": [], "task_graph": {"nodes": [], "edges": []}}
    assert bounded["bounds"]["tasks_total"] == 0
    assert bounded["bounds"]["graph_nodes_total"] == 0
    assert bound_snapshot({})["bounds"]["progress_total"] == 0
