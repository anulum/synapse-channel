# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — EventStore open-path idempotence and watermark receipt backfill tests
"""Verify that reopening an initialized EventStore performs no full events scan or write."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import __version__
from synapse_channel.core.at_rest_keys import generate_key_file
from synapse_channel.core.delivery_modes import DeliveryRefusal, parse_delivery_intent
from synapse_channel.core.delivery_persistence import DELIVERY_EVENT_KINDS
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    connect_sqlcipher,
    migrate_plaintext_to_sqlcipher,
    sqlcipher_available,
)
from synapse_channel.core.persistence_store_format import STORE_FORMAT, read_journal_instance

_WRITE_STATEMENT_PREFIXES = (
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "ALTER",
    "DROP",
)


class _RecordingConnection(sqlite3.Connection):
    """SQLite connection recording raw unexpanded SQL alongside ``set_trace_callback``."""

    raw_statements: list[tuple[str, Any]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.raw_statements = []

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        self.raw_statements.append((sql, parameters))
        return super().execute(sql, parameters)


def _trace_open(
    monkeypatch: pytest.MonkeyPatch,
    db: Path,
    *,
    key_file: Path | None = None,
) -> tuple[list[str], list[tuple[str, Any]], int, bool]:
    """Open ``EventStore(db)`` while tracing every SQL statement issued on its connection."""
    traced: list[str] = []
    raw_statements: list[tuple[str, Any]] = []
    captured: list[Any] = []
    if key_file is None:
        original_connect = sqlite3.connect

        def _tracing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            kwargs.setdefault("factory", _RecordingConnection)
            connection = original_connect(*args, **kwargs)
            assert isinstance(connection, _RecordingConnection)
            connection.set_trace_callback(traced.append)
            captured.append(connection)
            return connection

        monkeypatch.setattr(sqlite3, "connect", _tracing_connect)
        with EventStore(db):
            conn = captured[-1]
            total_changes = int(conn.total_changes)
            in_tx = bool(conn.in_transaction)
            raw_statements.extend(conn.raw_statements)
        return traced, raw_statements, total_changes, in_tx

    from synapse_channel.core import persistence_sqlcipher as sqlcipher_mod

    driver = sqlcipher_mod.import_sqlcipher_module()
    original_driver_connect = driver.connect

    def _tracing_driver_connect(*args: Any, **kwargs: Any) -> Any:
        connection = original_driver_connect(*args, **kwargs)
        connection.set_trace_callback(traced.append)
        captured.append(connection)
        return connection

    monkeypatch.setattr(driver, "connect", _tracing_driver_connect)
    with EventStore(db, key_file=key_file):
        total_changes = int(captured[-1].total_changes)
        in_tx = bool(captured[-1].in_transaction)
    return traced, raw_statements, total_changes, in_tx


def _write_statements(traced: list[str]) -> list[str]:
    """Return every traced SQL statement that begins a transaction or mutates data/schema."""
    writes: list[str] = []
    for sql in traced:
        stripped = sql.lstrip().upper()
        if stripped.startswith(_WRITE_STATEMENT_PREFIXES):
            writes.append(sql)
    return writes


def _full_event_scans(
    connection: Any,
    traced: list[str],
    raw_statements: Sequence[tuple[str, Any]] = (),
) -> list[tuple[str, str]]:
    """Return every traced or raw SELECT whose SQLite query plan performs a full ``SCAN events``."""
    scans: list[tuple[str, str]] = []
    for sql in traced:
        if not sql.lstrip().upper().startswith("SELECT "):
            continue
        for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall():
            detail = str(row[3])
            if re.search(r"\bSCAN\s+events\b", detail) and "USING INDEX" not in detail:
                scans.append((sql, detail))
    for raw_sql, params in raw_statements:
        if not raw_sql.lstrip().upper().startswith("SELECT "):
            continue
        for row in connection.execute(f"EXPLAIN QUERY PLAN {raw_sql}", params).fetchall():
            detail = str(row[3])
            if re.search(r"\bSCAN\s+events\b", detail) and "USING INDEX" not in detail:
                scans.append((raw_sql, detail))
    return scans


def _seed_store_with_receipt_and_delivery(db: Path) -> tuple[int, str]:
    """Populate a store with chat, directed receipt, and version-three delivery rows."""
    intent = parse_delivery_intent(
        {
            "sender": "spoofed",
            "type": "delivery_request",
            "target": "P/receiver",
            "protocol_version": 3,
            "request_id": "req-open-1",
            "idempotency_key": "idem-open-1",
            "target_incarnation": "b" * 64,
            "mode": "follow_up",
            "allowed_fallbacks": ["next_turn"],
            "task_id": "T-open-1",
            "body": "Verify indexed replay.",
            "deadline": 160.0,
        },
        sender="P/author",
        origin_hub="hub-open",
        now=100.0,
    )
    offer = {
        "type": "delivery_offer",
        "operation_key": intent.operation_key,
        "notification_id": f"delivery:{intent.operation_key}:1",
        "target": intent.target,
        "target_incarnation": intent.target_incarnation,
        "request_id": intent.request_id,
        "mode": "follow_up",
        "body": intent.body,
    }
    with EventStore(db) as store:
        for index in range(10):
            store.append("chat", {"sender": "P/author", "target": "ALL", "text": f"msg-{index}"})
        message_seq = store.commit_delivery_receipt_request(
            chat={"sender": "P/author", "target": "P/receiver", "msg_id": 11, "payload": "ping"},
            requested={"sender": "P/author", "target": "P/receiver", "message_id": 11},
        )
        store.commit_delivery_receipt_transition(
            kind="delivery_receipt_immediate",
            payload={
                "sender": "P/author",
                "target": "P/receiver",
                "message_id": 11,
                "message_seq": message_seq,
                "delivered": False,
            },
            notification={"type": "delivery_receipt", "target": "P/author", "delivered": False},
            state="pending",
        )
        store.delivery.create(intent, selected_mode="follow_up", quality="native", offer=offer)
    return message_seq, intent.operation_key


def test_second_open_issues_no_event_table_scan_and_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reopening an initialized store issues no full ``SCAN events`` and no write statement."""
    db = tmp_path / "events.db"
    message_seq, op_key = _seed_store_with_receipt_and_delivery(db)
    with closing(sqlite3.connect(db)) as observer:
        data_version_before = int(observer.execute("PRAGMA data_version").fetchone()[0])
        schema_version_before = int(observer.execute("PRAGMA schema_version").fetchone()[0])

        traced, raw_statements, total_changes, in_tx = _trace_open(monkeypatch, db)

        data_version_after = int(observer.execute("PRAGMA data_version").fetchone()[0])
        schema_version_after = int(observer.execute("PRAGMA schema_version").fetchone()[0])
        scans = _full_event_scans(observer, traced, raw_statements)

    assert _write_statements(traced) == []
    assert total_changes == 0
    assert not in_tx
    assert data_version_after == data_version_before
    assert schema_version_after == schema_version_before
    assert scans == []

    with EventStore(db) as reopened:
        aggregate = reopened.delivery_receipt_aggregate(message_seq)
        assert aggregate is not None and aggregate.state == "pending"
        assert reopened.delivery.get(op_key) is not None


def test_second_open_does_not_block_on_an_active_sqlite_writer_lock(tmp_path: Path) -> None:
    """Reopening an initialized store succeeds immediately while another connection holds write."""
    db = tmp_path / "contended.db"
    message_seq, _op_key = _seed_store_with_receipt_and_delivery(db)

    with closing(sqlite3.connect(db)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        try:
            with EventStore(db) as reopened:
                aggregate = reopened.delivery_receipt_aggregate(message_seq)
                assert aggregate is not None and aggregate.state == "pending"
        finally:
            writer.rollback()
        elapsed = time.perf_counter() - started
    assert elapsed < 2.0


@pytest.mark.parametrize("legacy_shape", ["no_store_format", "pre_watermark_store_format"])
def test_legacy_store_backfills_receipts_and_projects_post_watermark_sentinel_via_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_shape: str
) -> None:
    """Watermark backfill skips up-to-date opens and projects later raw receipts via index."""
    db = tmp_path / f"{legacy_shape}.db"
    if legacy_shape == "no_store_format":
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(
                "CREATE TABLE events ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                "kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO events VALUES (1, 1000.0, 'chat', ?)",
                (json.dumps({"sender": "P/alice", "target": "P/bob", "msg_id": 5}),),
            )
            conn.execute(
                "INSERT INTO events VALUES (2, 1000.1, 'delivery_receipt_requested', ?)",
                (
                    json.dumps(
                        {
                            "sender": "P/alice",
                            "target": "P/bob",
                            "message_id": 5,
                            "message_seq": 1,
                        }
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO events VALUES (3, 1000.2, 'delivery_receipt_immediate', ?)",
                (
                    json.dumps(
                        {
                            "sender": "P/alice",
                            "target": "P/bob",
                            "message_id": 5,
                            "message_seq": 1,
                            "delivered": False,
                        }
                    ),
                ),
            )
            conn.commit()
        expected_floor_release = __version__
        prior_instance = None
    else:
        with EventStore(db) as store:
            store.append("chat", {"sender": "P/alice", "target": "P/bob", "msg_id": 5})
            store.append(
                "delivery_receipt_requested",
                {"sender": "P/alice", "target": "P/bob", "message_id": 5, "message_seq": 1},
            )
            store.append(
                "delivery_receipt_immediate",
                {
                    "sender": "P/alice",
                    "target": "P/bob",
                    "message_id": 5,
                    "message_seq": 1,
                    "delivered": False,
                },
            )
            prior_instance = store.journal_instance()
        assert prior_instance is not None
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("DELETE FROM delivery_receipts")
            conn.execute("DROP TABLE store_format")
            conn.execute(
                "CREATE TABLE store_format ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "floor INTEGER NOT NULL, "
                "floor_release TEXT NOT NULL, "
                "last_writer_release TEXT NOT NULL, "
                "journal_instance TEXT, "
                "instance_floor INTEGER)"
            )
            conn.execute(
                "INSERT INTO store_format VALUES (1, ?, '0.99.64', '0.99.64', ?, ?)",
                (STORE_FORMAT, prior_instance.instance, prior_instance.floor),
            )
            conn.commit()
        expected_floor_release = "0.99.64"

    # First open on the legacy store runs backfill once and records watermark=3.
    with EventStore(db) as migrated:
        aggregate = migrated.delivery_receipt_aggregate(1)
        assert aggregate is not None
        assert aggregate.state == "pending"
        assert aggregate.delivered is False
        if prior_instance is not None:
            assert migrated.journal_instance() == prior_instance

    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT floor_release, receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone()
        assert row == (expected_floor_release, 3)

    # Clean reopen with nothing planted issues zero full scans and zero writes.
    traced_clean, raw_clean, changes_clean, in_tx_clean = _trace_open(monkeypatch, db)
    with closing(sqlite3.connect(db)) as conn:
        assert _full_event_scans(conn, traced_clean, raw_clean) == []
    assert _write_statements(traced_clean) == []
    assert changes_clean == 0
    assert not in_tx_clean

    # Plant a raw receipt event beyond the watermark (simulating an older pre-transactional
    # release writing a receipt event without updating delivery_receipts or the watermark).
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (2000.0, 'chat', ?)",
            (json.dumps({"sender": "P/alice", "target": "P/bob", "msg_id": 99}),),
        )
        sentinel_seq = int(conn.execute("SELECT max(seq) FROM events").fetchone()[0])
        conn.execute(
            "INSERT INTO events (ts, kind, payload) "
            "VALUES (2000.1, 'delivery_receipt_requested', ?)",
            (
                json.dumps(
                    {
                        "sender": "P/alice",
                        "target": "P/bob",
                        "message_id": 99,
                        "message_seq": sentinel_seq,
                    }
                ),
            ),
        )
        sentinel_receipt_seq = int(conn.execute("SELECT max(seq) FROM events").fetchone()[0])
        conn.commit()

    # Reopen projects the post-watermark sentinel through the partial index (no table scan!)
    # and advances receipt_backfill_through_seq atomically.
    traced_proj, raw_proj, changes_proj, in_tx_proj = _trace_open(monkeypatch, db)
    with closing(sqlite3.connect(db)) as conn:
        assert _full_event_scans(conn, traced_proj, raw_proj) == []
        wm_row = conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone()
        assert wm_row == (sentinel_receipt_seq,)
    assert changes_proj > 0
    assert not in_tx_proj

    with EventStore(db) as reopened:
        assert reopened.delivery_receipt_aggregate(1) is not None
        sentinel_agg = reopened.delivery_receipt_aggregate(sentinel_seq)
        assert sentinel_agg is not None
        assert sentinel_agg.state == "requested"
        assert sentinel_agg.updated_event_seq == sentinel_receipt_seq

    # Next reopen after advancing watermark issues zero scans and zero writes again.
    traced_after, raw_after, changes_after, in_tx_after = _trace_open(monkeypatch, db)
    with closing(sqlite3.connect(db)) as conn:
        assert _full_event_scans(conn, traced_after, raw_after) == []
    assert _write_statements(traced_after) == []
    assert changes_after == 0
    assert not in_tx_after


def test_interrupted_open_rolls_back_both_backfill_and_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted open rolls back both projected aggregates and watermark advancement."""
    db = tmp_path / "interrupted_legacy.db"
    payload = {"message_seq": 7, "message_id": 77, "sender": "P/sender", "target": "P/target"}
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "CREATE TABLE events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "kind TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (7, 1000.0, ?, ?)",
            ("delivery_receipt_requested", json.dumps(payload)),
        )
        conn.commit()

    def _interrupted_kinds() -> Iterator[str]:
        yield "chat"
        raise RuntimeError("interrupted open")

    with pytest.raises(RuntimeError, match="interrupted open"):
        EventStore(db, aef_outbox_kinds=_interrupted_kinds())

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT count(*) FROM delivery_receipts").fetchone() == (0,)
        columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(store_format)")}
        if "receipt_backfill_through_seq" in columns:
            marker = conn.execute(
                "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
            ).fetchone()
            assert marker is None or marker[0] is None

    # Clean open now completes backfill and sets watermark=7 atomically.
    with EventStore(db, aef_outbox_kinds=("chat",)) as recovered:
        aggregate = recovered.delivery_receipt_aggregate(7)
        assert aggregate is not None and aggregate.state == "requested"

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone() == (7,)

    # Subsequent open issues zero scans and zero writes.
    traced, raw_statements, total_changes, in_tx = _trace_open(monkeypatch, db)
    with closing(sqlite3.connect(db)) as conn:
        assert _full_event_scans(conn, traced, raw_statements) == []
    assert _write_statements(traced) == []
    assert total_changes == 0
    assert not in_tx

    # Interrupted reopen after planting a later sentinel rolls back both aggregate and watermark.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO events (seq, ts, kind, payload) VALUES (9, 1001.0, ?, ?)",
            (
                "delivery_receipt_requested",
                json.dumps(
                    {"message_seq": 9, "message_id": 99, "sender": "P/sender", "target": "P/target"}
                ),
            ),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="interrupted open"):
        EventStore(db, aef_outbox_kinds=_interrupted_kinds())

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM delivery_receipts WHERE message_seq = 9"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone() == (7,)

    # Clean reopen projects sentinel 9 and advances watermark to 9.
    with EventStore(db, aef_outbox_kinds=("chat",)) as recovered2:
        agg9 = recovered2.delivery_receipt_aggregate(9)
        assert agg9 is not None and agg9.state == "requested"

    # Simulated crash leaving projected row 9 in place while watermark lags at 7
    # re-projects row 9 idempotently on reopen and advances watermark back to 9.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE store_format SET receipt_backfill_through_seq = 7 WHERE id = 1")
        conn.commit()
    with EventStore(db, aef_outbox_kinds=("chat",)) as idempotent_reopen:
        agg9_again = idempotent_reopen.delivery_receipt_aggregate(9)
        assert agg9_again is not None and agg9_again.state == "requested"
        assert agg9_again.updated_event_seq == 9
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM delivery_receipts WHERE message_seq = 9"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone() == (9,)


def test_receipt_and_delivery_queries_stay_in_sync_with_kinds_and_use_partial_indexes(
    tmp_path: Path,
) -> None:
    """Both partial indexes and literal SQL queries match canonical event-kind sets."""
    from synapse_channel.core import delivery_replay, persistence_receipts

    db = tmp_path / "indexes.db"
    _message_seq, op_key = _seed_store_with_receipt_and_delivery(db)

    # 1. Delivery replay query + partial index stay in sync with DELIVERY_EVENT_KINDS.
    replay_sql = getattr(delivery_replay, "_DELIVERY_REPLAY_EVENTS_SQL", "")
    assert replay_sql, "delivery_replay must use a static literal query matching the partial index"
    assert set(re.findall(r"'([^']+)'", replay_sql)) == set(DELIVERY_EVENT_KINDS)

    # 2. Receipt backfill & gap queries + partial index stay in sync with EventKind receipt kinds.
    expected_receipt_kinds = {
        EventKind.DELIVERY_RECEIPT_REQUESTED,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        EventKind.DELIVERY_RECEIPT_DEFERRED,
        EventKind.DELIVERY_RECEIPT_EXPIRED,
    }
    assert set(persistence_receipts.RECEIPT_BACKFILL_EVENT_KINDS) == expected_receipt_kinds
    backfill_sql = getattr(persistence_receipts, "_RECEIPT_BACKFILL_EVENTS_SQL", "")
    gap_sql = getattr(persistence_receipts, "_RECEIPT_UNCOVERED_GAP_SQL", "")
    assert backfill_sql and gap_sql
    assert set(re.findall(r"'([^']+)'", backfill_sql)) == expected_receipt_kinds
    assert set(re.findall(r"'([^']+)'", gap_sql)) == expected_receipt_kinds

    with closing(sqlite3.connect(db)) as conn:
        replay_idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'events_delivery_intent_kind_seq_idx'"
        ).fetchone()
        assert replay_idx is not None and replay_idx[0] is not None
        assert set(re.findall(r"'([^']+)'", str(replay_idx[0]))) == set(DELIVERY_EVENT_KINDS)
        replay_plan = [str(r[3]) for r in conn.execute(f"EXPLAIN QUERY PLAN {replay_sql}")]
        assert any("events_delivery_intent_kind_seq_idx" in step for step in replay_plan)
        assert not any(
            re.search(r"\bSCAN\s+events\b", step) and "USING INDEX" not in step
            for step in replay_plan
        )

        receipt_idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'events_delivery_receipt_kind_seq_idx'"
        ).fetchone()
        assert receipt_idx is not None and receipt_idx[0] is not None
        assert set(re.findall(r"'([^']+)'", str(receipt_idx[0]))) == expected_receipt_kinds
        backfill_plan = [
            str(r[3]) for r in conn.execute(f"EXPLAIN QUERY PLAN {backfill_sql}", (0,))
        ]
        assert any("events_delivery_receipt_kind_seq_idx" in step for step in backfill_plan)
        assert not any(
            re.search(r"\bSCAN\s+events\b", step) and "USING INDEX" not in step
            for step in backfill_plan
        )
        gap_plan = [str(r[3]) for r in conn.execute(f"EXPLAIN QUERY PLAN {gap_sql}", (0, 10))]
        assert any("events_delivery_receipt_kind_seq_idx" in step for step in gap_plan)

        # Tamper with aggregate stage and prove fail-closed replay audit still fires on open.
        conn.execute(
            "UPDATE delivery_requests SET stage = 'completed' WHERE operation_key = ?",
            (op_key,),
        )
        conn.commit()

    with pytest.raises(DeliveryRefusal):
        EventStore(db)


def test_transactional_receipt_does_not_leapfrog_earlier_raw_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transactional receipt commit must not advance the watermark past an unprojected raw row."""
    from synapse_channel.core.persistence_receipts import _advance_watermark_if_contiguous

    db = tmp_path / "gap.db"
    with EventStore(db) as store:
        raw_chat_seq = store.append("chat", {"sender": "P/alice", "target": "P/bob", "msg_id": 1})
        store.append(
            "delivery_receipt_requested",
            {
                "sender": "P/alice",
                "target": "P/bob",
                "message_id": 1,
                "message_seq": raw_chat_seq,
            },
        )
        tx_chat_seq = store.commit_delivery_receipt_request(
            chat={"sender": "P/alice", "target": "P/bob", "msg_id": 2},
            requested={"sender": "P/alice", "target": "P/bob", "message_id": 2},
        )
        assert store.delivery_receipt_aggregate(raw_chat_seq) is None
        assert store.delivery_receipt_aggregate(tx_chat_seq) is not None

    # Watermark stayed at 0 because raw_chat_seq's receipt was unprojected in the gap.
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone() == (0,)

    # Reopen projects the raw receipt via the partial index and advances the watermark.
    with EventStore(db) as reopened:
        raw_agg = reopened.delivery_receipt_aggregate(raw_chat_seq)
        tx_agg = reopened.delivery_receipt_aggregate(tx_chat_seq)
        assert raw_agg is not None and raw_agg.state == "requested"
        assert tx_agg is not None and tx_agg.state == "requested"

    # Malformed receipt event advances watermark past itself so later reopens stay write-free.
    malformed_db = tmp_path / "malformed.db"
    with EventStore(malformed_db) as mstore:
        bad_seq = mstore.append("delivery_receipt_requested", {"bad": True})
    with EventStore(malformed_db):
        pass
    with closing(sqlite3.connect(malformed_db)) as conn:
        assert conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone() == (bad_seq,)
    traced, raw_statements, total_changes, in_tx = _trace_open(monkeypatch, malformed_db)
    with closing(sqlite3.connect(malformed_db)) as conn:
        assert _full_event_scans(conn, traced, raw_statements) == []
    assert _write_statements(traced) == []
    assert total_changes == 0
    assert not in_tx

    # Direct helper guard branches: no watermark and non-advancing seq are write-free no-ops.
    with closing(sqlite3.connect(":memory:")) as mem:
        mem.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)"
        )
        _advance_watermark_if_contiguous(mem, 5)
        assert mem.total_changes == 0
    with closing(sqlite3.connect(malformed_db)) as conn:
        _advance_watermark_if_contiguous(conn, 0)
        assert conn.total_changes == 0


def test_sqlcipher_migrated_store_preserves_receipt_backfill_watermark_and_skips_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLCipher export keeps modern watermarks and leaves legacy stores unmarked."""
    if not sqlcipher_available():
        return
    key = generate_key_file(tmp_path / "hub.key")
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    message_seq, _op_key = _seed_store_with_receipt_and_delivery(plain)
    migrate_plaintext_to_sqlcipher(plain, enc, key_file=key)

    with closing(connect_sqlcipher(enc, key.read_bytes())) as conn:
        row = conn.execute(
            "SELECT receipt_backfill_through_seq FROM store_format WHERE id = 1"
        ).fetchone()
        assert row is not None and isinstance(row[0], int) and row[0] > message_seq
        assert read_journal_instance(conn) is not None

    traced, raw_statements, total_changes, in_tx = _trace_open(monkeypatch, enc, key_file=key)
    with closing(connect_sqlcipher(enc, key.read_bytes())) as conn:
        assert _full_event_scans(conn, traced, raw_statements) == []
    assert _write_statements(traced) == []
    assert total_changes == 0
    assert not in_tx
    with EventStore(enc, key_file=key) as reopened:
        assert reopened.delivery_receipt_aggregate(message_seq) is not None

    # A legacy plaintext store without the watermark stays unmarked through SQLCipher export
    # so its first EventStore open runs backfill through the partial index.
    legacy_plain = tmp_path / "legacy_plain.db"
    legacy_enc = tmp_path / "legacy_enc.db"
    with closing(sqlite3.connect(legacy_plain)) as conn:
        conn.execute(
            "CREATE TABLE events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "kind TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO events VALUES (1, 1000.0, 'chat', ?)",
            (json.dumps({"sender": "P/alice", "target": "P/bob", "msg_id": 3}),),
        )
        conn.execute(
            "INSERT INTO events VALUES (2, 1000.1, 'delivery_receipt_requested', ?)",
            (
                json.dumps(
                    {
                        "sender": "P/alice",
                        "target": "P/bob",
                        "message_id": 3,
                        "message_seq": 1,
                    }
                ),
            ),
        )
        conn.commit()
    migrate_plaintext_to_sqlcipher(legacy_plain, legacy_enc, key_file=key)
    with EventStore(legacy_enc, key_file=key) as migrated_enc:
        agg = migrated_enc.delivery_receipt_aggregate(1)
        assert agg is not None and agg.state == "requested"


def test_receipt_backfill_watermark_is_read_only_and_idempotent(tmp_path: Path) -> None:
    """Reading the watermark never writes, and re-recording an equal/lower seq is a no-op."""
    from synapse_channel.core.persistence_store_format import (
        receipt_backfill_watermark,
        record_receipt_backfill_watermark,
    )

    legacy = tmp_path / "legacy.db"
    with closing(sqlite3.connect(legacy)) as conn:
        conn.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)"
        )
        conn.commit()
    before_legacy = hashlib.sha256(legacy.read_bytes()).hexdigest()
    with closing(sqlite3.connect(legacy)) as conn:
        assert receipt_backfill_watermark(conn) is None
        assert conn.total_changes == 0
        assert not conn.in_transaction
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before_legacy

    modern = tmp_path / "modern.db"
    with EventStore(modern):
        pass
    before_modern = hashlib.sha256(modern.read_bytes()).hexdigest()
    with closing(sqlite3.connect(modern)) as conn:
        assert receipt_backfill_watermark(conn) == 0
        assert record_receipt_backfill_watermark(conn, through_seq=0) == 0
        assert conn.total_changes == 0
        assert not conn.in_transaction
    assert hashlib.sha256(modern.read_bytes()).hexdigest() == before_modern

    # Watermark only moves forward: a lower through_seq never regresses an advanced watermark.
    with closing(sqlite3.connect(modern)) as conn:
        assert record_receipt_backfill_watermark(conn, through_seq=5) == 5
        conn.commit()
    before_monotone = hashlib.sha256(modern.read_bytes()).hexdigest()
    with closing(sqlite3.connect(modern)) as conn:
        assert record_receipt_backfill_watermark(conn, through_seq=2) == 5
        assert receipt_backfill_watermark(conn) == 5
        assert conn.total_changes == 0
        assert not conn.in_transaction
    assert hashlib.sha256(modern.read_bytes()).hexdigest() == before_monotone
