# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — seeded fleet boards for the bounded snapshot tests
"""Build fleet snapshots shaped like a long-running hub's board."""

from __future__ import annotations

from typing import Any

from synapse_channel.dashboard import DashboardSnapshot

STATUS_PLAN = (
    ("done", 600),
    ("cancelled", 50),
    ("open", 250),
    ("in_progress", 50),
    ("blocked", 50),
)
"""Task statuses of the seeded board: 650 closed and 350 open of 1,000."""


def seeded_tasks(plan: tuple[tuple[str, int], ...] = STATUS_PLAN) -> list[dict[str, Any]]:
    """Return tasks in ``plan`` order; every blocked task depends on a done one.

    Parameters
    ----------
    plan : tuple[tuple[str, int], ...], optional
        ``(status, count)`` pairs, in board order.

    Returns
    -------
    list[dict[str, Any]]
        Ledger task rows with unique ids ``T0000``, ``T0001`` and so on.
    """
    tasks: list[dict[str, Any]] = []
    for status, count in plan:
        for _ in range(count):
            number = len(tasks)
            tasks.append(
                {
                    "task_id": f"T{number:04d}",
                    "title": f"Seeded task {number} with a title of ordinary length",
                    "status": status,
                    "owner": f"SEED/agent-{number % 7}",
                    "depends_on": [],
                    "paths": [f"src/module_{number % 40}.py"],
                }
            )
    blocked = [task for task in tasks if task["status"] == "blocked"]
    for index, task in enumerate(blocked):
        task["depends_on"] = [tasks[index]["task_id"]]
    return tasks


def seeded_progress(count: int = 5000) -> list[dict[str, Any]]:
    """Return ``count`` progress notes; every tenth is an assessment receipt."""
    return [
        {
            "task_id": f"T{index % 1000:04d}",
            "author": f"SEED/agent-{index % 7}",
            "kind": "assessment" if index % 10 == 0 else "note",
            "text": (
                f"release receipt: evidence=pytest tests/test_seed_{index}.py -q"
                if index % 10 == 0
                else f"progress note {index}: a status line of ordinary length for a board"
            ),
            "posted_at": 1_783_000_000.0 + index,
        }
        for index in range(count)
    ]


def seeded_snapshot(
    *, plan: tuple[tuple[str, int], ...] = STATUS_PLAN, notes: int = 5000
) -> DashboardSnapshot:
    """Return a snapshot of a board seeded per ``plan`` with ``notes`` progress notes."""
    return DashboardSnapshot(
        online_agents=["SEED/agent-0", "SEED/agent-0-rx"],
        state={"active_claims": []},
        board={"tasks": seeded_tasks(plan), "progress": seeded_progress(notes)},
        manifest=[],
        hub_version="test",
        hub_id="hub-seed",
        config_epoch="epoch-seed",
    )
