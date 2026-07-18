# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for journal replay of ledger project/version fields

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import record_ledger_task, replay
from synapse_channel.core.ledger import LedgerTask
from synapse_channel.core.persistence import EventStore


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def test_record_ledger_task_replays_project_and_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_ledger_task(
        store,
        LedgerTask(
            task_id="PLAN",
            title="Plan",
            created_at=1000.0,
            updated_at=1001.0,
            description="do the work",
            depends_on=("READY",),
            status="blocked",
            suggested_owner="A",
            project="SYNAPSE-CHANNEL",
            version=7,
            created_by="planner",
        ),
    )
    result = replay(store, now=2000.0)
    store.close()
    task = result.blackboard.tasks["PLAN"]
    assert task.project == "SYNAPSE-CHANNEL"
    assert task.version == 7
    assert task.title == "Plan"
    assert task.status == "blocked"


def test_replay_of_legacy_payload_without_scope_fields_defaults(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(
        "ledger_task",
        {
            "task_id": "OLD",
            "title": "legacy",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "status": "open",
            "suggested_owner": "",
            "created_by": "planner",
        },
        durable=True,
    )
    result = replay(store, now=2000.0)
    store.close()
    task = result.blackboard.tasks["OLD"]
    assert task.project == ""
    assert task.version == 1


def test_replayed_task_keeps_mutating_with_bumped_versions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_ledger_task(
        store,
        LedgerTask(
            task_id="PLAN",
            title="Plan",
            created_at=1000.0,
            updated_at=1001.0,
            project="PROJ",
            version=3,
            created_by="planner",
        ),
    )
    result = replay(store, now=2000.0)
    store.close()
    board = result.blackboard
    ok, _ = board.update_task("PLAN", status="done", expected_version=3, now=2001.0)
    assert ok
    assert board.tasks["PLAN"].version == 4
