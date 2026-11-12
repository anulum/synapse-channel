# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable incomplete stream recovery tests
"""Qualify atomic deadlines, migration, replay, bounded state and crash recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.activity_streams import StreamActivity
from synapse_channel.mcp.activity_page import ActivityItem, ActivityPage
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError, WakeScope
from synapse_channel.mcp.wake_streams import MAX_PENDING_STREAMS


def ledger(root: Path) -> WakeLedger:
    return WakeLedger(root, WakeScope("subject", "ALPHA", "ALPHA/worker", "b" * 64, "hub-1"))


def stream(seq: int, *, key: str = "a" * 64, at: int = 100, terminal: bool = False) -> ActivityItem:
    return ActivityItem(
        seq, "messages" if terminal else "streams", StreamActivity(seq, key, terminal, at)
    )


def page(cursor: int, *items: ActivityItem, **changes: Any) -> ActivityPage:
    fields: dict[str, Any] = {
        "identity": "ALPHA/worker",
        "hub_id": "hub-1",
        "cursor": cursor,
        "items": items,
        "has_more": False,
    }
    fields.update(changes)
    return ActivityPage(**fields)


def test_progress_restarts_terminal_clears_and_stalled_stream_wakes_once(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    outbox = ledger(root)
    assert outbox.recovery_cursor() == 0
    p = page(2, stream(1), stream(2, at=120))
    assert not outbox.observe_recovery_page(0, p, now=125)
    assert not outbox.observe_recovery_page(0, p, now=200)
    assert not outbox.expire_streams(now=419)
    outbox = ledger(root)
    assert outbox.recovery_cursor() == 2 and outbox.expire_streams(now=420) == 1
    assert not outbox.expire_streams(now=421)
    attempt = outbox.acquire(421)
    assert attempt is not None and json.loads(attempt.body)["changes"] == ["messages"]
    assert "aaaa" not in attempt.body.decode()
    assert outbox.finish(attempt, 422, "accepted", http_status=202)
    assert not outbox.observe_recovery_page(2, page(3, stream(3, key="b" * 64, at=500)), now=500)
    assert outbox.observe_recovery_page(3, page(4, stream(4, key="b" * 64, terminal=True)), now=510)
    assert outbox.expire_streams(now=1000) == 0


def test_legacy_backfill_does_not_repeat_old_messages_but_recovers_old_partial(
    tmp_path: Path,
) -> None:
    outbox = ledger(tmp_path)
    assert outbox.observe_page(0, 4, sources=("messages",))
    old = outbox.acquire(100)
    assert old is not None and outbox.finish(old, 101, "accepted", http_status=202)
    assert not outbox.observe_recovery_page(
        0, page(2, ActivityItem(1, "messages"), stream(2)), now=200
    )
    assert not outbox.observe_recovery_page(2, page(4, ActivityItem(4, "messages")), now=200)
    assert outbox.acquire(200) is None
    assert outbox.observe_recovery_page(4, page(5, ActivityItem(5, "tasks")), now=200)
    assert outbox.expire_streams(now=400) == 1
    next_attempt = outbox.acquire(401)
    assert next_attempt and json.loads(next_attempt.body)["changes"] == ["messages", "tasks"]
    assert outbox.scan_cursor() == 4 and outbox.recovery_cursor() == 5


def test_terminal_on_later_page_removes_old_stream_before_expiry(tmp_path: Path) -> None:
    outbox = ledger(tmp_path)
    assert not outbox.observe_recovery_page(0, page(1, stream(1), has_more=True), now=1000)
    assert outbox.observe_recovery_page(1, page(2, stream(2, terminal=True)), now=1000)
    assert not outbox.expire_streams(now=1000)


def test_future_timestamp_clamped_and_clock_regression_cannot_shorten_deadline(
    tmp_path: Path,
) -> None:
    outbox = ledger(tmp_path)
    assert not outbox.observe_recovery_page(0, page(1, stream(1, at=999999)), now=100)
    assert not outbox.observe_recovery_page(1, page(2, stream(2, at=90)), now=90)
    assert outbox.expire_streams(now=399) == 0 and outbox.expire_streams(now=400) == 1


@pytest.mark.parametrize("now", [True, -1, MAX_WAKE_INTEGER, "1"])
def test_bad_clock_refuses_without_file(tmp_path: Path, now: Any) -> None:
    outbox = ledger(tmp_path / "ledger")
    calls: tuple[Callable[[], bool | int], ...] = (
        lambda: outbox.observe_recovery_page(0, page(1, stream(1)), now=now),
        lambda: outbox.expire_streams(now=now),
    )
    for call in calls:
        with pytest.raises(WakeLedgerError, match="timestamp"):
            call()
    assert not outbox.path.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": "OTHER/worker"},
        {"hub_id": "other"},
        {"cursor": True},
        {"cursor": -1},
        {"items": []},
        {"items": (ActivityItem(1, "messages"),) * 101},
    ],
)
def test_bad_page_refuses_before_storage(tmp_path: Path, changes: dict[str, Any]) -> None:
    outbox = ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="page"):
        outbox.observe_recovery_page(0, page(**({"cursor": 1} | changes)), now=100)
    assert not outbox.path.exists()


@pytest.mark.parametrize(
    "items",
    [
        (ActivityItem(True, "messages"),),
        (ActivityItem(0, "messages"),),
        (ActivityItem(1, "streams"),),
        (ActivityItem(1, "tasks", StreamActivity(1, "a" * 64, False, 1)),),
        (ActivityItem(1, "streams", StreamActivity(2, "a" * 64, False, 1)),),
    ],
)
def test_bad_item_refuses_before_storage(tmp_path: Path, items: tuple[ActivityItem, ...]) -> None:
    outbox = ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="sequence|family"):
        outbox.observe_recovery_page(0, page(1, *items), now=100)
    assert not outbox.path.exists()


def test_stale_contradictory_page_and_failing_sql_cannot_consume_progress(tmp_path: Path) -> None:
    outbox = ledger(tmp_path)
    outbox.observe_recovery_page(0, page(1, stream(1)), now=100)
    with pytest.raises(WakeLedgerError, match="position changed"):
        outbox.observe_recovery_page(0, page(1, stream(1, key="b" * 64)), now=100)
    with closing(sqlite3.connect(outbox.path)) as c, c:
        c.execute(
            "CREATE TRIGGER refuse BEFORE UPDATE ON meta BEGIN SELECT RAISE(ABORT,'fault'); END"
        )
    with pytest.raises(WakeLedgerError):
        outbox.observe_recovery_page(1, page(2, stream(2, terminal=True)), now=200)
    assert outbox.recovery_cursor() == 1
    with pytest.raises(WakeLedgerError):
        outbox.expire_streams(now=400)
    with closing(sqlite3.connect(outbox.path)) as c, c:
        c.execute("DROP TRIGGER refuse")
    assert outbox.expire_streams(now=400) == 1
    assert outbox.recovery_cursor() == 1


def test_capacity_refusal_preserves_cursor_and_every_existing_deadline(tmp_path: Path) -> None:
    outbox = ledger(tmp_path)
    outbox.recovery_cursor()
    deadlines = {f"{i:064x}": 500 for i in range(MAX_PENDING_STREAMS)}
    with closing(sqlite3.connect(outbox.path)) as c, c:
        c.execute("INSERT INTO sources VALUES ('stream-progress',?)", (json.dumps(deadlines),))
    with pytest.raises(WakeLedgerError, match="capacity exhausted"):
        outbox.observe_recovery_page(0, page(1, stream(1, key="f" * 64)), now=100)
    assert outbox.recovery_cursor() == 0
    assert outbox.expire_streams(now=500) == MAX_PENDING_STREAMS


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("stream-progress", "not-json"),
        ("stream-progress", "[]"),
        ("stream-progress", "x" * 160001),
        ("stream-progress", json.dumps({"bad": 1})),
        ("stream-progress", json.dumps({"a" * 64: True})),
        ("stream-recovery", "{}"),
        (
            "stream-recovery",
            json.dumps({"previous": 2, "cursor": 1, "floor": 0, "digest": "a" * 64}),
        ),
        ("stream-recovery", json.dumps({"previous": 0, "cursor": 1, "floor": 0, "digest": 1})),
        ("stream-recovery", json.dumps({"previous": 0, "cursor": 1, "floor": 0, "digest": "bad"})),
        ("activity", "{}"),
        ("activity", json.dumps({"previous": 0, "cursor": True, "bits": 1})),
        ("activity", json.dumps({"previous": 2, "cursor": 1, "bits": 1})),
        ("activity", json.dumps({"previous": 0, "cursor": 1, "bits": 8})),
    ],
)
def test_corrupt_state_refuses_instead_of_resetting(
    tmp_path: Path, source: str, value: str
) -> None:
    outbox = ledger(tmp_path)
    outbox.recovery_cursor()
    with closing(sqlite3.connect(outbox.path)) as c, c:
        c.execute("INSERT INTO sources VALUES (?,?)", (source, value))
    with pytest.raises(WakeLedgerError):
        outbox.observe_recovery_page(0, page(1, stream(1)), now=100)


def test_revision_exhaustion_rolls_back_deadlines_and_cursor(tmp_path: Path) -> None:
    outbox = ledger(tmp_path)
    outbox.recovery_cursor()
    with closing(sqlite3.connect(outbox.path)) as c, c:
        c.execute("UPDATE meta SET revision=?", (MAX_WAKE_INTEGER,))
    with pytest.raises(WakeLedgerError, match="revision budget"):
        outbox.observe_recovery_page(0, page(1, stream(1, terminal=True)), now=100)
    assert outbox.recovery_cursor() == 0


def test_schema_two_upgrade_preserves_legacy_cursor_receipts_and_pending_work(
    tmp_path: Path,
) -> None:
    outbox = ledger(tmp_path)
    outbox.observe_page(0, 4, sources=("messages",))
    attempt = outbox.acquire(100)
    assert attempt is not None
    assert outbox.finish(attempt, 101, "rejected", http_status=401)
    with closing(sqlite3.connect(outbox.path)) as database, database:
        database.execute("PRAGMA user_version=2")
        before = database.execute("SELECT * FROM deliveries").fetchall()
    assert outbox.recovery_cursor() == 0
    assert outbox.scan_cursor() == 4 and outbox.summary()["unresolved_failures"] == 1
    with closing(sqlite3.connect(outbox.path)) as database, database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 4
        assert database.execute("SELECT * FROM deliveries").fetchall() == before


def test_failed_scope_verification_rolls_back_schema_two_upgrade(tmp_path: Path) -> None:
    outbox = ledger(tmp_path)
    outbox.recovery_cursor()
    with closing(sqlite3.connect(outbox.path)) as database, database:
        database.execute("PRAGMA user_version=2")
        database.execute("UPDATE meta SET hub='foreign'")
    with pytest.raises(WakeLedgerError, match="identity changed"):
        outbox.recovery_cursor()
    with closing(sqlite3.connect(outbox.path)) as database, database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2


def test_real_process_exit_retains_expiry_and_pending_hint_without_native_ack(
    tmp_path: Path,
) -> None:
    script = (
        "import os,sys\nfrom pathlib import Path\n"
        "from synapse_channel.mcp.wake_ledger import WakeLedger\n"
        "from synapse_channel.mcp.wake_pointer import WakeScope\n"
        "from synapse_channel.mcp.activity_page import ActivityPage,ActivityItem\n"
        "from synapse_channel.core.activity_streams import StreamActivity\n"
        "outbox=WakeLedger(Path(sys.argv[1]),WakeScope('subject','ALPHA','ALPHA/worker','b'*64,'hub-1'))\n"
        "p=ActivityPage('ALPHA/worker','hub-1',1,"
        "(ActivityItem(1,'streams',StreamActivity(1,'a'*64,False,100)),),False)\n"
        "outbox.observe_recovery_page(0,p,now=100)\n"
        "assert outbox.expire_streams(now=400)==1\nos._exit(0)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert done.returncode == 0 and done.stderr == b""
    outbox = ledger(tmp_path)
    assert outbox.recovery_cursor() == 1 and outbox.expire_streams(now=500) == 0
    assert outbox.acquire(500) is not None
