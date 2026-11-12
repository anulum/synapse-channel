# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable historical notification recovery tests
"""Recover historical work once through a real SQLite ledger and a real process exit."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.pending_work import PendingItem
from synapse_channel.mcp.pending_work import PendingSnapshot
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError, WakeScope


def snapshot(*items: PendingItem, **changes: Any) -> PendingSnapshot:
    values: dict[str, Any] = {"identity": "ALPHA/worker", "hub_id": "hub", "items": items}
    return PendingSnapshot(**(values | changes))


def ledger(root: Path) -> WakeLedger:
    return WakeLedger(root, WakeScope("subject", "ALPHA", "ALPHA/worker", "b" * 64, "hub"))


def test_historical_receipts_are_atomic_deduplicated_and_do_not_forget_completed_work(
    tmp_path: Path,
) -> None:
    outbox = ledger(tmp_path)
    initial = snapshot(PendingItem("handoffs", "a" * 64), PendingItem("tasks", "b" * 64))
    assert outbox.observe_pending_work(initial)
    assert not ledger(tmp_path).observe_pending_work(initial)
    attempt = outbox.acquire(100)
    assert attempt and json.loads(attempt.body)["changes"] == ["tasks", "handoffs"]
    assert outbox.finish(attempt, 101, "accepted", http_status=202)
    assert not outbox.observe_pending_work(snapshot())
    assert not outbox.observe_pending_work(initial)
    assert outbox.acquire(102) is None and outbox.scan_cursor() == 0
    with pytest.raises(WakeLedgerError, match="family changed"):
        outbox.observe_pending_work(snapshot(PendingItem("tasks", "a" * 64)))


@pytest.mark.parametrize(
    "changes", [{"identity": "BETA/worker"}, {"hub_id": "foreign"}, {"items": []}]
)
def test_foreign_or_invalid_snapshot_creates_no_file(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    outbox = ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="invalid historical"):
        outbox.observe_pending_work(snapshot(**changes))
    assert not outbox.path.exists()


@pytest.mark.parametrize(
    "raw", ["not json", b"[]", "x" * 131073, "{}", '[{"kind":"tasks","key":"private"}]']
)
def test_corrupt_pending_state_is_not_reset(tmp_path: Path, raw: Any) -> None:
    outbox = ledger(tmp_path)
    outbox.summary()
    with closing(sqlite3.connect(outbox.path)) as database, database:
        database.execute("INSERT INTO sources VALUES ('pending-legacy',?)", (raw,))
    with pytest.raises(WakeLedgerError, match="invalid stored"):
        outbox.observe_pending_work(snapshot(PendingItem("tasks", "a" * 64)))


@pytest.mark.parametrize("fault", ["sql", "revision", "capacity"])
def test_failed_recovery_cannot_consume_seen_markers_or_pending_bits(
    tmp_path: Path, fault: str
) -> None:
    outbox = ledger(tmp_path)
    outbox.summary()
    with closing(sqlite3.connect(outbox.path)) as database, database:
        if fault == "sql":
            database.execute(
                "CREATE TRIGGER fail_pending BEFORE UPDATE ON meta BEGIN "
                "SELECT RAISE(ABORT,'private failure'); END"
            )
        elif fault == "revision":
            database.execute("UPDATE meta SET revision=?", (MAX_WAKE_INTEGER,))
        else:
            known = [{"kind": "tasks", "key": f"{i:064x}"} for i in range(1024)]
            database.execute(
                "INSERT INTO sources VALUES ('pending-legacy',?)", (json.dumps(known),)
            )
    with pytest.raises(WakeLedgerError):
        outbox.observe_pending_work(snapshot(PendingItem("tasks", "f" * 64)))
    assert outbox.summary()["pending_sources"] == 0
    with closing(sqlite3.connect(outbox.path)) as database, database:
        row = database.execute(
            "SELECT position FROM sources WHERE source='pending-legacy'"
        ).fetchone()
        assert (len(json.loads(row[0])) == 1024) if fault == "capacity" else row is None


def test_actual_process_exit_keeps_seen_fingerprints_and_pending_recovery(tmp_path: Path) -> None:
    script = (
        "import os,sys\nfrom pathlib import Path\n"
        "from synapse_channel.mcp.wake_ledger import WakeLedger\n"
        "from synapse_channel.mcp.wake_pointer import WakeScope\n"
        "from synapse_channel.mcp.pending_work import PendingSnapshot\n"
        "from synapse_channel.core.pending_work import PendingItem\n"
        "outbox=WakeLedger(Path(sys.argv[1]),WakeScope('subject','ALPHA','ALPHA/worker','b'*64,'hub'))\n"
        "assert outbox.observe_pending_work(PendingSnapshot('ALPHA/worker','hub',"
        "(PendingItem('tasks','a'*64),)))\n"
        "os._exit(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == b""
    outbox = ledger(tmp_path)
    assert not outbox.observe_pending_work(snapshot(PendingItem("tasks", "a" * 64)))
    attempt = outbox.acquire(100)
    assert attempt and json.loads(attempt.body)["changes"] == ["tasks"]
