# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic activity scan and outbox recovery tests
"""Exercise page progress and all notification kinds through real private SQLite."""

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

from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError, WakeScope


def _ledger(root: Path) -> WakeLedger:
    return WakeLedger(root, WakeScope("subject", "ALPHA", "ALPHA/worker", "b" * 64, "hub-1"))


def test_all_kinds_commit_once_and_survive_reopen(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert ledger.scan_cursor() == 0
    assert ledger.observe_page(0, 15, sources=("messages", "tasks", "handoffs"))
    assert not ledger.observe_page(0, 15, sources=("messages", "tasks", "handoffs"))
    assert not ledger.observe_page(15, 15, sources=())
    assert ledger.summary()["revision"] == 1
    reopened = _ledger(tmp_path / "ledger")
    assert reopened.scan_cursor() == 15
    attempt = reopened.acquire(100)
    assert attempt is not None
    assert json.loads(attempt.body)["changes"] == ["messages", "tasks", "handoffs"]
    assert reopened.observe_page(15, 20, sources=("tasks",))
    assert reopened.finish(attempt, 102, "accepted", http_status=202)
    newer = reopened.acquire(103)
    assert newer is not None and newer.delivery_id != attempt.delivery_id
    assert json.loads(newer.body)["changes"] == ["tasks"]
    assert reopened.scan_cursor() == 20


def test_empty_scans_advance_without_rewriting_last_replay(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert not ledger.observe_page(0, 0, sources=())
    assert ledger.scan_cursor() == 0
    assert not ledger.observe_page(0, 4, sources=())
    assert not ledger.observe_page(0, 4, sources=())
    assert not ledger.observe_page(4, 4, sources=())
    assert ledger.scan_cursor() == 4 and ledger.acquire(100) is None
    assert ledger.summary()["revision"] == 0


def test_stale_and_contradictory_commits_preserve_existing_pending_work(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert ledger.observe_page(0, 4, sources=("messages",))
    with pytest.raises(WakeLedgerError, match="scan position changed"):
        ledger.observe_page(0, 4, sources=("handoffs",))
    assert ledger.observe_page(4, 7, sources=("tasks",))
    with pytest.raises(WakeLedgerError, match="scan position changed"):
        ledger.observe_page(0, 4, sources=("messages",))
    assert ledger.scan_cursor() == 7
    assert ledger.summary()["revision"] == 2 and ledger.summary()["pending_sources"] == 3


def test_real_sqlite_failure_rolls_back_cursor_and_every_source_bit(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert ledger.scan_cursor() == 0
    with closing(sqlite3.connect(ledger.path)) as database, database:
        database.execute(
            "CREATE TRIGGER refuse_pending BEFORE UPDATE ON meta BEGIN "
            "SELECT RAISE(ABORT,'private storage failure sentinel'); END"
        )
    with pytest.raises(WakeLedgerError) as failure:
        ledger.observe_page(0, 8, sources=("messages", "tasks", "handoffs"))
    assert str(failure.value) == "wake ledger unavailable"
    assert "sentinel" not in str(failure.value)
    assert ledger.scan_cursor() == 0
    assert ledger.summary()["revision"] == ledger.summary()["pending_sources"] == 0
    with closing(sqlite3.connect(ledger.path)) as database, database:
        assert database.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        database.execute("DROP TRIGGER refuse_pending")
    assert ledger.observe_page(0, 8, sources=("messages", "tasks", "handoffs"))
    assert ledger.scan_cursor() == 8 and ledger.summary()["pending_sources"] == 7


def test_revision_exhaustion_cannot_commit_half_a_page(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "ledger")
    assert ledger.scan_cursor() == 0
    with closing(sqlite3.connect(ledger.path)) as database, database:
        database.execute("UPDATE meta SET revision=?", (MAX_WAKE_INTEGER,))
    with pytest.raises(WakeLedgerError, match="revision budget exhausted"):
        ledger.observe_page(0, 9, sources=("tasks", "handoffs"))
    assert ledger.scan_cursor() == 0
    assert ledger.summary()["revision"] == MAX_WAKE_INTEGER
    assert ledger.summary()["pending_sources"] == 0


def test_actual_process_exit_after_commit_keeps_all_three_pending_sources(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    script = (
        "import os,sys\n"
        "from pathlib import Path\n"
        "from synapse_channel.mcp.wake_ledger import WakeLedger\n"
        "from synapse_channel.mcp.wake_pointer import WakeScope\n"
        "ledger=WakeLedger(Path(sys.argv[1]),"
        "WakeScope('subject','ALPHA','ALPHA/worker','b'*64,'hub-1'))\n"
        "ledger.observe_page(0,42,sources=('messages','tasks','handoffs'))\n"
        "print(ledger.scan_cursor(),flush=True)\n"
        "os._exit(0)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0 and completed.stdout == b"42\n"
    assert completed.stderr == b""
    ledger = _ledger(root)
    assert ledger.scan_cursor() == 42
    attempt = ledger.acquire(100)
    assert attempt is not None
    assert json.loads(attempt.body)["changes"] == ["messages", "tasks", "handoffs"]


@pytest.mark.parametrize(
    "sources",
    [
        [],
        ("messages", "messages"),
        ("invalid",),
        (True,),
        ([],),
        ("messages", "tasks", "handoffs", "messages"),
    ],
)
def test_invalid_source_sets_refuse_before_creating_storage(tmp_path: Path, sources: Any) -> None:
    ledger = _ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="invalid wake activity sources"):
        ledger.observe_page(0, 1, sources=sources)
    assert not ledger.path.exists()


@pytest.mark.parametrize(
    ("previous", "cursor", "sources"),
    [
        (True, 1, ()),
        (-1, 1, ()),
        (0, True, ()),
        (0, -1, ()),
        (0, MAX_WAKE_INTEGER + 1, ()),
        (2, 1, ()),
        (0, 0, ("messages",)),
    ],
)
def test_invalid_scan_intervals_refuse_before_storage(
    tmp_path: Path, previous: Any, cursor: Any, sources: Any
) -> None:
    ledger = _ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="invalid wake activity scan"):
        ledger.observe_page(previous, cursor, sources=sources)
    assert not ledger.path.exists()


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        "{}",
        "x" * 257,
        b"{}",
        '{"previous":0,"cursor":true,"bits":0}',
        '{"previous":4,"cursor":1,"bits":0}',
        '{"previous":0,"cursor":1,"bits":8}',
        '{"previous":1,"cursor":1,"bits":1}',
    ],
)
def test_corrupt_scan_is_not_reset_to_zero(tmp_path: Path, raw: object) -> None:
    ledger = _ledger(tmp_path / "ledger")
    ledger.scan_cursor()
    with closing(sqlite3.connect(ledger.path)) as database, database:
        database.execute("INSERT INTO sources VALUES ('activity',?)", (raw,))
    with pytest.raises(WakeLedgerError, match="invalid stored wake activity scan"):
        ledger.scan_cursor()
    with pytest.raises(WakeLedgerError, match="invalid stored wake activity scan"):
        ledger.observe_page(0, 5, sources=("messages",))
