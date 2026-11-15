# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a seeded fleet of two projects, unscoped work and bare names
"""A fleet shaped like the ecosystem's hub, small enough to count by hand.

Two projects (``ALPHA``, ``BETA``), two tasks with no project, a bare agent, a
terminal identity, and one claim that a ``user/`` terminal holds on a ``BETA``
task, so the union rule for claims is exercised.
"""

from __future__ import annotations

from synapse_channel.dashboard import DashboardSnapshot

AGENTS = ["ALPHA/worker", "ALPHA/reviewer", "BETA/worker", "solo", "user/terminal-1"]


def _task(task_id: str, project: str, status: str) -> dict[str, object]:
    """Return one board task as the hub's ledger writes it."""
    return {
        "task_id": task_id,
        "title": task_id,
        "status": status,
        "project": project,
        "depends_on": [],
    }


def scope_fleet() -> DashboardSnapshot:
    """Return the seeded snapshot; every call returns equal, independent data."""
    return DashboardSnapshot(
        online_agents=list(AGENTS),
        state={
            "active_claims": [
                {"task_id": "A1", "owner": "ALPHA/worker", "scope": "src/a/"},
                {"task_id": "B1", "owner": "user/terminal-1", "scope": "src/b/"},
                {"task_id": "free", "owner": "solo", "scope": "notes/"},
            ],
            "agents": [{"agent": name, "last_seen": 1_783_000_000.0} for name in AGENTS],
            "generated_at": 1_783_000_000.0,
        },
        board={
            "tasks": [
                _task("A1", "ALPHA", "open"),
                _task("A2", "ALPHA", "done"),
                _task("B1", "BETA", "in_progress"),
                _task("N1", "", "open"),
                _task("N2", "", "blocked"),
            ],
            "ready": ["A1", "N1"],
            "progress": [
                {"task_id": task_id, "author": "solo", "kind": "note", "text": f"note on {task_id}"}
                for task_id in ("A1", "B1", "N1")
            ],
        },
        manifest=[],
        hub_id="hub-scopes",
        agent_roles={"ALPHA/worker": ["ALPHA/builder"], "BETA/worker": ["BETA/builder"]},
        agent_liveness={"ALPHA/worker": {"live": True}, "solo": {"live": True}},
        wake_capabilities={"ALPHA/worker": "active", "BETA/worker": "active"},
    )
