# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard task dependency graph tests

from __future__ import annotations

from synapse_channel.dashboard_task_graph import (
    build_task_dependency_graph,
    render_task_dependency_graph_html,
)


def test_task_dependency_graph_derives_nodes_edges_and_unmet_dependencies() -> None:
    board = {
        "tasks": [
            {"task_id": "BUILD", "title": "Build <core>", "status": "done"},
            {
                "task_id": "TEST",
                "title": "Run tests",
                "status": "blocked",
                "depends_on": ["BUILD", "LINT", "MISSING"],
            },
            {"task_id": "LINT", "title": "Lint", "status": "open"},
            {
                "task_id": "SHIP",
                "title": "Ship",
                "status": "blocked",
                "depends_on": ["TEST"],
            },
        ],
        "ready": ["LINT"],
    }

    graph = build_task_dependency_graph(board).to_dict()

    assert graph == {
        "nodes": [
            {"task_id": "BUILD", "title": "Build <core>", "status": "done", "ready": False},
            {"task_id": "LINT", "title": "Lint", "status": "open", "ready": True},
            {"task_id": "SHIP", "title": "Ship", "status": "blocked", "ready": False},
            {"task_id": "TEST", "title": "Run tests", "status": "blocked", "ready": False},
        ],
        "edges": [
            {
                "from": "BUILD",
                "to": "TEST",
                "satisfied": True,
                "missing": False,
                "from_status": "done",
            },
            {
                "from": "LINT",
                "to": "TEST",
                "satisfied": False,
                "missing": False,
                "from_status": "open",
            },
            {
                "from": "MISSING",
                "to": "TEST",
                "satisfied": False,
                "missing": True,
                "from_status": "missing",
            },
            {
                "from": "TEST",
                "to": "SHIP",
                "satisfied": False,
                "missing": False,
                "from_status": "blocked",
            },
        ],
        "blocked": [
            {"task_id": "SHIP", "blocked_by": ["TEST"]},
            {"task_id": "TEST", "blocked_by": ["LINT", "MISSING"]},
        ],
        "ready": ["LINT"],
        "total_tasks": 4,
        "total_edges": 4,
    }


def test_task_dependency_graph_html_escapes_and_handles_empty_board() -> None:
    html = render_task_dependency_graph_html(
        {
            "tasks": [
                {"task_id": "A<script>", "title": "<unsafe>", "status": "open"},
                {
                    "task_id": "B",
                    "title": "Blocked",
                    "status": "blocked",
                    "depends_on": ["A<script>"],
                },
            ],
            "ready": ["A<script>"],
        }
    )

    assert "Task dependency graph" in html
    assert "<unsafe>" not in html
    assert "&lt;unsafe&gt;" in html
    assert "A&lt;script&gt;" in html
    assert "A<script>" not in html

    empty_html = render_task_dependency_graph_html({"tasks": [], "ready": []})
    assert "No task dependencies" in empty_html


def test_task_dependency_graph_handles_malformed_board_values() -> None:
    graph = build_task_dependency_graph({"tasks": "not-a-list", "ready": "not-a-list"})

    assert graph.to_dict() == {
        "nodes": [],
        "edges": [],
        "blocked": [],
        "ready": [],
        "total_tasks": 0,
        "total_edges": 0,
    }


def test_task_dependency_graph_html_labels_missing_prerequisites() -> None:
    html = render_task_dependency_graph_html(
        {
            "tasks": [
                {
                    "task_id": "WAITING",
                    "title": "Waiting",
                    "status": "blocked",
                    "depends_on": ["UNKNOWN"],
                }
            ],
            "ready": [],
        }
    )

    assert "UNKNOWN" in html
    assert "missing; prerequisite status: missing" in html
