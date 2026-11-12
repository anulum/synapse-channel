# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable wake pointer lifecycle
"""Exercise actual private SQLite custody without treating a wake as work completion."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.secure_path import assert_owner_only_file_path
from synapse_channel.mcp.wake_database import MAX_WAKE_DATABASE_BYTES, MAX_WAKE_RECEIPTS
from synapse_channel.mcp.wake_ledger import WAKE_MAX_ATTEMPTS, WakeLedger
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_BODY_BYTES,
    MAX_WAKE_INTEGER,
    WakeAttempt,
    WakeLedgerError,
    WakeScope,
)


def _scope() -> WakeScope:
    """Use a synthetic fixed operator binding, never a real account credential."""
    return WakeScope("subject", "PROJECT", "PROJECT/receiver", "a" * 64, "hub-a")


def _ledger(tmp_path: Path) -> WakeLedger:
    """Give each test its own private state directory."""
    return WakeLedger(tmp_path / "wake-state", _scope())


def _first(ledger: WakeLedger, now: int = 100) -> WakeAttempt:
    """Commit a real journal scan before acquiring a dispatch attempt."""
    assert ledger.observe("messages", None, 7, relevant=True)
    delivery = ledger.acquire(now)
    assert delivery is not None
    return delivery


def test_restart_retry_keeps_id_and_bytes_and_coalesces_newer_sources(tmp_path: Path) -> None:
    """Separate newer changes from the already dispatched immutable pointer."""
    ledger = _ledger(tmp_path)
    first = _first(ledger)
    assert ledger.observe("tasks", None, "b" * 64, relevant=True)
    assert ledger.observe("handoffs", None, "c" * 64, relevant=True)
    assert ledger.observe("messages", 7, 12, relevant=True)
    restored = _ledger(tmp_path)
    assert restored.acquire(109) is None
    second = restored.acquire(110)
    assert second is not None
    assert (second.delivery_id, second.body) == (first.delivery_id, first.body)
    assert second.attempt == 2
    assert not restored.finish(first, 111, "accepted", http_status=202)
    assert restored.finish(second, 111, "accepted", http_status=202)
    newer = restored.acquire(112)
    assert newer is not None
    assert newer.delivery_id != first.delivery_id
    assert json.loads(newer.body)["changes"] == ["messages", "tasks", "handoffs"]
    assert json.loads(first.body)["changes"] == ["messages"]
    assert restored.position("messages") == 12
    assert restored.position("tasks") == "b" * 64
    assert restored.position("handoffs") == "c" * 64
    assert restored.summary()["transport_accepted"] == 1


def test_empty_pages_advance_without_creating_notifications(tmp_path: Path) -> None:
    """A scanned native cursor is distinct from notification or processing state."""
    ledger = _ledger(tmp_path)
    assert ledger.position("messages") is None
    assert not ledger.observe("messages", None, 20, relevant=False)
    assert ledger.position("messages") == 20
    assert not ledger.observe("messages", None, 20, relevant=True)
    assert ledger.acquire(100) is None
    assert ledger.summary()["revision"] == 0
    with pytest.raises(WakeLedgerError, match="scan position changed"):
        ledger.observe("messages", None, 21, relevant=True)
    with pytest.raises(WakeLedgerError, match="regressed"):
        ledger.observe("messages", 20, 19, relevant=True)
    assert ledger.position("messages") == 20


def test_finite_backoff_and_deadletter_survive_reopening(tmp_path: Path) -> None:
    """Transient failures keep one ID and exhaust into a separate transport receipt."""
    ledger = _ledger(tmp_path)
    current = _first(ledger)
    first_id, body = current.delivery_id, current.body
    assert ledger.summary()["next_attempt_at"] == 110
    now = 100
    for attempt, delay in enumerate((1, 5, 30), start=1):
        assert current.attempt == attempt
        assert ledger.finish(current, now, "retry", http_status=503)
        ledger = _ledger(tmp_path)
        assert ledger.summary()["next_attempt_at"] == now + delay
        assert ledger.acquire(now + delay - 1) is None
        next_attempt = ledger.acquire(now + delay)
        assert next_attempt is not None
        current = next_attempt
        assert (current.delivery_id, current.body) == (first_id, body)
        now += delay
    assert current.attempt == WAKE_MAX_ATTEMPTS
    assert ledger.finish(current, now, "retry")
    assert ledger.acquire(now + 100) is None
    assert ledger.summary()["dead_lettered"] == 1
    assert ledger.summary()["transport_accepted"] == 0
    assert ledger.position("messages") == 7


def test_abandoned_final_attempt_does_not_retry_forever(tmp_path: Path) -> None:
    """Expired attempt fences preserve the same delivery and eventually stop."""
    ledger = _ledger(tmp_path)
    current = _first(ledger)
    original_id = current.delivery_id
    for now in (110, 120, 130):
        next_attempt = ledger.acquire(now)
        assert next_attempt is not None and next_attempt.delivery_id == original_id
        current = next_attempt
    assert ledger.acquire(140) is None
    assert ledger.acquire(150) is None
    assert ledger.summary()["dead_lettered"] == 1


def test_a_late_answer_cannot_revive_an_abandoned_final_attempt(tmp_path: Path) -> None:
    """A sender that answers after its last lease ran out changes nothing."""
    ledger = _ledger(tmp_path)
    current = _first(ledger)
    for now in (110, 120, 130):
        next_attempt = ledger.acquire(now)
        assert next_attempt is not None
        current = next_attempt
    assert ledger.acquire(140) is None
    before = ledger.inspect()
    assert [row["state"] for row in before["deliveries"]] == ["dead-letter"]
    assert before["summary"]["dead_lettered"] == 1
    assert before["summary"]["transport_accepted"] == 0
    assert not ledger.finish(current, 141, "accepted", http_status=202)
    assert ledger.inspect() == before


def test_outcome_is_fenced_and_does_not_consume_scan_state(tmp_path: Path) -> None:
    """Only one exact attempt can change the local transport counters."""
    ledger = _ledger(tmp_path)
    current = _first(ledger)
    forged = dataclasses.replace(current, body=b"unrelated")
    assert not ledger.finish(forged, 101, "accepted", http_status=202)
    with pytest.raises(WakeLedgerError, match="invalid wake attempt outcome"):
        ledger.finish(current, 101, "accepted", http_status=503)
    assert ledger.finish(current, 101, "rejected", http_status=401)
    assert not ledger.finish(current, 102, "accepted", http_status=202)
    assert ledger.position("messages") == 7
    assert ledger.summary()["dead_lettered"] == 1


def test_process_exit_after_acquire_keeps_original_dispatch(tmp_path: Path) -> None:
    """An abrupt real process exit does not regenerate the next retry's ID or body."""
    root = tmp_path / "wake-state"
    script = (
        "import os,json,sys;"
        "from pathlib import Path;"
        "from synapse_channel.mcp.wake_ledger import WakeLedger;"
        "from synapse_channel.mcp.wake_pointer import WakeScope;"
        "q=WakeLedger(Path(sys.argv[1]),WakeScope('subject','PROJECT',"
        "'PROJECT/receiver','a'*64,'hub-a'));"
        "q.observe('messages',None,7,relevant=True);a=q.acquire(100);"
        "print(json.dumps({'id':a.delivery_id,'body':a.body.hex()}),flush=True);"
        "os._exit(0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    before = json.loads(result.stdout)
    after = _ledger(tmp_path).acquire(110)
    assert after is not None
    assert after.delivery_id == before["id"]
    assert after.body.hex() == before["body"]


def test_only_terminal_receipts_are_retired_under_a_fixed_budget(tmp_path: Path) -> None:
    """Receipt rollover leaves exact aggregate counts and never evicts active work."""
    ledger = _ledger(tmp_path)
    previous: int | None = None
    for position in range(1, MAX_WAKE_RECEIPTS + 4):
        assert ledger.observe("messages", previous, position, relevant=True)
        delivery = ledger.acquire(position)
        assert delivery is not None
        assert ledger.finish(delivery, position, "accepted", http_status=202)
        previous = position
    summary = ledger.summary()
    assert summary["retained_receipts"] == MAX_WAKE_RECEIPTS
    assert summary["retired_receipts"] == 3
    assert summary["transport_accepted"] == MAX_WAKE_RECEIPTS + 3
    assert summary["pending_sources"] == 0


def test_hub_change_and_foreign_file_are_refused(tmp_path: Path) -> None:
    """A fresh connection may not silently reinterpret an old native cursor."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    changed = dataclasses.replace(_scope(), hub_id="hub-b")
    with pytest.raises(WakeLedgerError, match="identity changed"):
        WakeLedger(ledger.path.parent, changed).summary()
    assert _ledger(tmp_path).position("messages") == 7


def test_busy_transaction_refuses_without_advancing_scan(tmp_path: Path) -> None:
    """A real SQLite writer lock blocks mutation without hiding or dropping its event."""
    ledger = _ledger(tmp_path)
    assert not ledger.observe("messages", None, 0, relevant=False)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(WakeLedgerError, match="ledger unavailable"):
            ledger.observe("messages", 0, 1, relevant=True)
    assert ledger.position("messages") == 0
    assert ledger.acquire(100) is None


@pytest.mark.parametrize("contents", [b"private-canary-not-sqlite", b""])
def test_foreign_or_corrupt_file_refuses_without_echo(tmp_path: Path, contents: bytes) -> None:
    """A nonempty foreign format is not reset into an empty successful queue."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    if contents:
        ledger.path.write_bytes(contents)
    else:
        with closing(sqlite3.connect(ledger.path)) as connection, connection:
            connection.execute("PRAGMA user_version=5")
    with pytest.raises(WakeLedgerError) as error:
        ledger.summary()
    assert "private-canary" not in str(error.value)


def test_owner_privacy_and_file_budget_are_real(tmp_path: Path) -> None:
    """Reject an oversized owned file before SQLite can read or mutate it."""
    ledger = _ledger(tmp_path)
    ledger.summary()
    assert_owner_only_file_path(ledger.path, purpose="test wake ledger", require_single_link=True)
    with ledger.path.open("r+b") as stream:
        stream.truncate(MAX_WAKE_DATABASE_BYTES + 1)
    with pytest.raises(WakeLedgerError, match="byte budget"):
        ledger.summary()


@pytest.mark.parametrize("bad", [True, -1, 9223372036854775808, "not-a-sequence"])
def test_invalid_scan_positions_do_not_create_dispatches(tmp_path: Path, bad: Any) -> None:
    """Reject invalid public values without coercing them into native positions."""
    ledger = _ledger(tmp_path)
    with pytest.raises(WakeLedgerError, match="position"):
        ledger.observe("messages", None, bad, relevant=True)
    assert ledger.acquire(100) is None


def test_projection_markers_are_digests_and_pointers_have_no_native_body(tmp_path: Path) -> None:
    """The only persisted projection payload is its digest and a fixed wake hint."""
    ledger = _ledger(tmp_path)
    digest = hashlib.sha256(b"private native task text").hexdigest()
    assert ledger.observe("tasks", None, digest, relevant=True)
    delivery = ledger.acquire(100)
    assert delivery is not None
    body = json.loads(delivery.body)
    assert body["changes"] == ["tasks"]
    assert set(body) == {"version", "id", "project", "seat", "hub_id", "revision", "changes"}
    assert b"private native task text" not in ledger.path.read_bytes()
    assert b"private native task text" not in delivery.body


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", ""),
        ("subject", "x" * 257),
        ("project", "A/B"),
        ("project", " PROJECT"),
        ("seat", "OTHER/receiver"),
        ("seat", "PROJECT/"),
        ("binding", "not-a-digest"),
        ("hub_id", ""),
    ],
)
def test_invalid_identity_never_opens_storage(field: str, value: Any) -> None:
    """Refuse malformed operator identity bindings without coercion or aliasing."""
    with pytest.raises(WakeLedgerError, match="identity binding"):
        dataclasses.replace(_scope(), **{field: value})


@pytest.mark.parametrize(
    "source,previous,position,relevant",
    [
        ("tasks", None, "bad", True),
        ("handoffs", None, 1, True),
        ("other", None, "a" * 64, True),
        ("messages", True, 1, True),
        ("messages", None, 1, 1),
    ],
)
def test_invalid_observations_leave_no_ledger(
    tmp_path: Path, source: Any, previous: Any, position: Any, relevant: Any
) -> None:
    """Reject invalid public observation values before touching durable state."""
    ledger = _ledger(tmp_path)
    with pytest.raises(WakeLedgerError):
        ledger.observe(source, previous, position, relevant=relevant)
    assert not ledger.path.exists()


@pytest.mark.parametrize("now", [True, -1, 1.5, MAX_WAKE_INTEGER])
def test_invalid_clock_values_cannot_claim_an_attempt(tmp_path: Path, now: Any) -> None:
    """Only bounded integer UTC times can create a persisted dispatch fence."""
    ledger = _ledger(tmp_path)
    with pytest.raises(WakeLedgerError, match="timestamp"):
        ledger.acquire(now)
    assert not ledger.path.exists()


@pytest.mark.parametrize(
    "delivery_id,revision,bits",
    [
        (None, 1, 1),
        ("bad", 1, 1),
        ("msg_" + "a" * 32, 0, 1),
        ("msg_" + "a" * 32, True, 1),
        ("msg_" + "a" * 32, 1, 0),
        ("msg_" + "a" * 32, 1, True),
    ],
)
def test_pointer_requires_real_bounded_identifiers(
    delivery_id: Any, revision: Any, bits: Any
) -> None:
    """Malformed local records cannot become a signed external payload."""
    with pytest.raises(WakeLedgerError, match="delivery pointer"):
        _scope().body(delivery_id, revision, bits)


def test_valid_unicode_identity_keeps_the_pointer_bounded() -> None:
    """Even maximally escaped admitted identity fields cannot grow an arbitrary body."""
    scope = WakeScope("s", "💠" * 120, "💠" * 120 + "/" + "💠" * 120, "a" * 64, "💠" * 256)
    assert len(scope.body("msg_" + "a" * 32, MAX_WAKE_INTEGER, 7)) < MAX_WAKE_BODY_BYTES


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown", 0), ("retry", True), ("retry", -1), ("retry", 1), ("retry", 600)],
)
def test_invalid_completion_keeps_the_attempt_live(
    tmp_path: Path, outcome: Any, status: Any
) -> None:
    """Bad child-result values cannot change a dispatch or its counters."""
    ledger = _ledger(tmp_path)
    original = _first(ledger)
    with pytest.raises(WakeLedgerError, match="outcome"):
        ledger.finish(original, 101, outcome, http_status=status)
    assert ledger.acquire(102) is None
    assert ledger.summary()["transport_accepted"] == 0


@pytest.mark.parametrize(
    "column,value",
    [
        ("id", b"bad-id"),
        ("revision", "bad"),
        ("bits", "bad"),
        ("body", "bad-text"),
        ("body", b"corrupt-canary"),
        ("attempt", 0),
        ("attempt", 5),
        ("deadline", -1),
        ("deadline", "bad"),
        ("due", -1),
        ("due", "bad"),
    ],
)
def test_corrupt_active_record_refuses_before_dispatch(
    tmp_path: Path, column: str, value: Any
) -> None:
    """Actual on-disk record damage is refused without a new attempt or body echo."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute(f"UPDATE deliveries SET {column}=?", (value,))
    before = ledger.path.read_bytes()
    with pytest.raises(WakeLedgerError) as error:
        ledger.acquire(101)
    assert "corrupt-canary" not in str(error.value)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("position", ["not-json", "{}", "7.5"])
def test_corrupt_scan_marker_is_not_defaulted_to_zero(tmp_path: Path, position: str) -> None:
    """A broken saved source position does not silently replay with an invented cursor."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("UPDATE sources SET position=?", (position,))
    with pytest.raises(WakeLedgerError, match="position"):
        ledger.position("messages")


def test_unknown_source_position_is_refused_before_file_creation(tmp_path: Path) -> None:
    """An unrecognized observer cannot enroll another storage lane."""
    ledger = _ledger(tmp_path)
    source: Any = "private-other-source"
    with pytest.raises(WakeLedgerError, match="source"):
        ledger.position(source)
    assert not ledger.path.exists()


def test_foreign_sqlite_schema_is_not_overwritten(tmp_path: Path) -> None:
    """A valid SQLite header alone does not authorize initialization over other data."""
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir(mode=0o700)
    ledger.path.touch(mode=0o600)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
    with pytest.raises(WakeLedgerError, match="schema"):
        ledger.summary()


def test_incompatible_real_page_size_is_refused(tmp_path: Path) -> None:
    """An actual differently paged database cannot bypass the fixed byte calculation."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("PRAGMA page_size=8192")
        connection.execute("VACUUM")
    with pytest.raises(WakeLedgerError, match="page size"):
        ledger.summary()


@pytest.mark.parametrize(
    "damage", ["missing-meta", "counter-type", "pending-bits", "bad-state", "body-budget"]
)
def test_corrupt_population_is_refused_without_discarding_it(tmp_path: Path, damage: str) -> None:
    """Do not turn malformed metadata or stored receipt populations into a clean queue."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        if damage == "missing-meta":
            connection.execute("DELETE FROM meta")
        elif damage == "counter-type":
            connection.execute("UPDATE meta SET accepted='bad'")
        elif damage == "pending-bits":
            connection.execute("UPDATE meta SET pending=8")
        elif damage == "bad-state":
            connection.execute("UPDATE deliveries SET state='unknown'")
        else:
            connection.execute("UPDATE deliveries SET body=?", (b"x" * (MAX_WAKE_BODY_BYTES + 1),))
    before = ledger.path.read_bytes()
    with pytest.raises(WakeLedgerError):
        ledger.summary()
    assert ledger.path.read_bytes() == before


def test_duplicate_active_population_is_refused(tmp_path: Path) -> None:
    """Even damaged uniqueness metadata cannot authorize two active dispatches."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("DROP INDEX active_delivery")
        connection.execute(
            "INSERT INTO deliveries(id,revision,bits,body,state,attempt,deadline,due) "
            "SELECT ?,revision,bits,body,state,attempt,deadline,due FROM deliveries",
            ("msg_" + "b" * 32,),
        )
    with pytest.raises(WakeLedgerError, match="population"):
        ledger.acquire(100)


def test_revision_exhaustion_rolls_back_the_scan_marker(tmp_path: Path) -> None:
    """Do not consume a new native page if its wake cannot be represented."""
    ledger = _ledger(tmp_path)
    _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("UPDATE meta SET revision=?", (MAX_WAKE_INTEGER,))
    with pytest.raises(WakeLedgerError, match="revision budget"):
        ledger.observe("messages", 7, 8, relevant=True)
    assert ledger.position("messages") == 7


def test_receipt_counter_exhaustion_preserves_the_inflight_fence(tmp_path: Path) -> None:
    """Counter overflow cannot publish a partial local receipt transaction."""
    ledger = _ledger(tmp_path)
    first = _first(ledger)
    with closing(sqlite3.connect(ledger.path)) as connection, connection:
        connection.execute("UPDATE meta SET accepted=?", (MAX_WAKE_INTEGER,))
    with pytest.raises(WakeLedgerError, match="counters"):
        ledger.finish(first, 101, "accepted", http_status=202)
    assert ledger.summary()["transport_accepted"] == MAX_WAKE_INTEGER
    assert ledger.acquire(102) is None


def test_unknown_completion_cannot_create_a_receipt(tmp_path: Path) -> None:
    """A token from another local delivery cannot settle this outbox."""
    ledger = _ledger(tmp_path)
    first = _first(ledger)
    unknown = dataclasses.replace(first, delivery_id="msg_" + "b" * 32)
    assert not ledger.finish(unknown, 101, "accepted", http_status=202)
    assert ledger.summary()["transport_accepted"] == 0
