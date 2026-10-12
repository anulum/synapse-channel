# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real read-only receipt projection tests
"""Exercise existing files, live WAL, operator damage and authenticated readers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.event_row_mac import RowMacKey, row_mac_key_path
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_receipt_view import (
    ReceiptProjectionError,
    read_delivery_receipt,
)


def _request(store: EventStore) -> int:
    """Commit the public directed chat/receipt transaction used by consumers."""
    return store.commit_delivery_receipt_request(
        chat={"sender": "P/sender", "target": "P/receiver", "payload": "hello"},
        requested={
            "sender": "P/sender",
            "target": "P/receiver",
            "message_id": 17,
            "client_msg_id": "client-17",
        },
    )


def _transition(store: EventStore, seq: int, state: str) -> None:
    """Commit each real receipt phase through the public transaction API."""
    from typing import cast

    from synapse_channel.core.persistence import DeliveryReceiptState

    kind = {
        "immediate_delivered": "delivery_receipt_immediate",
        "pending": "delivery_receipt_immediate",
        "deferred": "delivery_receipt_deferred",
        "expired": "delivery_receipt_expired",
    }[state]
    store.commit_delivery_receipt_transition(
        kind=kind,
        state=cast(DeliveryReceiptState, state),
        payload={
            "message_seq": seq,
            "message_id": 17,
            "sender": "P/sender",
            "target": "P/receiver",
            "delivered": state in {"immediate_delivered", "deferred"},
            "deferred": state in {"deferred", "expired"},
            "recipients": ["P/receiver"],
            "recipient_wake_capabilities": {"P/receiver": "passive"},
        },
        notification={"target": "P/sender"},
    )


@pytest.mark.parametrize(
    "state", ["requested", "immediate_delivered", "pending", "deferred", "expired"]
)
def test_receipt_projection_follows_live_wal_without_altering_the_store(
    tmp_path: Path, state: str
) -> None:
    """Read every phase from live WAL, preserving schema, permissions and tip."""
    path = tmp_path / "receipt # with space.db"
    with EventStore(path) as store:
        seq = _request(store)
        if state in {"deferred", "expired"}:
            _transition(store, seq, "pending")
        if state != "requested":
            _transition(store, seq, state)
        tip = store.max_seq()
        permissions = path.stat().st_mode
        view = read_delivery_receipt(path, seq)
        assert view is not None and view.state == state
        assert view.receipt["message_seq"] == seq
        assert view.receipt["target"] == "P/receiver"
        assert read_delivery_receipt(path, seq + 100) is None
        assert store.max_seq() == tip
        assert path.stat().st_mode == permissions
        if state == "requested":
            _transition(store, seq, "immediate_delivered")
            latest = read_delivery_receipt(path, seq)
            assert latest is not None and latest.state == "immediate_delivered"
            assert view.state == "requested"
            assert latest.receipt["recipients"] == ["P/receiver"]
    before = hashlib.sha256(path.read_bytes()).digest()
    assert read_delivery_receipt(path, seq) is not None
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_absent_and_unknown_sources_remain_unavailable_and_unmodified(tmp_path: Path) -> None:
    """Neither a missing path nor a legacy schema is bootstrapped by a reader."""
    absent = tmp_path / "not-created" / "hub.db"
    assert read_delivery_receipt(absent, 1) is None
    assert not absent.parent.exists()
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY)")
    before = path.read_bytes()
    assert read_delivery_receipt(path, 1) is None
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE events")
        connection.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)"
        )
    assert read_delivery_receipt(path, 1) is None
    path.write_bytes(b"this is not a SQLite database")
    assert read_delivery_receipt(path, 1) is None


@pytest.mark.parametrize("sequence", [True, 0, -1, 1.5, "1", 2**63])
def test_invalid_chat_sequence_is_rejected_before_any_open(
    tmp_path: Path, sequence: object
) -> None:
    """Only positive integer durable chat cursors are accepted."""
    from typing import cast

    with pytest.raises(ValueError, match="positive integer"):
        read_delivery_receipt(tmp_path / "absent.db", cast(int, sequence))
    assert list(tmp_path.iterdir()) == []


def test_reader_recovers_after_a_real_exclusive_lock(tmp_path: Path) -> None:
    """A busy look is bounded and the next look sees the released database."""
    path = tmp_path / "locked.db"
    with EventStore(path) as store:
        seq = _request(store)
    blocker = sqlite3.connect(path, timeout=0)
    try:
        blocker.execute("PRAGMA journal_mode=DELETE")
        blocker.execute("BEGIN EXCLUSIVE")
        assert read_delivery_receipt(path, seq) is None
    finally:
        blocker.rollback()
        blocker.close()
    assert read_delivery_receipt(path, seq) is not None


def test_old_mac_schema_is_not_migrated_and_owner_upgrade_restores_the_projection(
    tmp_path: Path,
) -> None:
    """An old source stays unavailable until its writer installs the MAC index."""
    path = tmp_path / "old-mac.db"
    with EventStore(path) as store:
        seq = _request(store)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX event_row_mac_idx")
    before = path.read_bytes()
    assert read_delivery_receipt(path, seq) is None
    assert path.read_bytes() == before
    with EventStore(path):
        assert read_delivery_receipt(path, seq) is not None
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX event_row_mac_idx")
        connection.execute("ALTER TABLE events DROP COLUMN mac")
    assert read_delivery_receipt(path, seq) is not None


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        '{"message_seq":true}',
        '{"message_seq":1.0,"message_id":17}',
        '{"message_seq":999,"message_id":17}',
        '{"message_seq":1,"message_id":true}',
        '{"message_seq":1,"message_id":17.0}',
        '{"message_seq":1,"message_id":999}',
        '{"message_seq":1,"message_id":17,"sender":"foreign","target":"P/receiver"}',
        '{"message_seq":1,"message_id":17,"sender":"P/sender","target":"foreign"}',
        '{"message_seq":1,"message_id":17,"sender":"P/sender","target":"P/receiver","extra":NaN}',
        '{"message_seq":1,"message_id":17,"sender":"P/sender","target":"P/receiver","extra":Infinity}',
        '{"message_seq":1,"message_id":17,"sender":"P/sender","target":"P/receiver","extra":-Infinity}',
        '{"extra":' + "[" * 2000 + "0" + "]" * 2000 + "}",
    ],
)
def test_damaged_or_foreign_receipt_payload_is_unavailable(tmp_path: Path, payload: str) -> None:
    """A joined aggregate can never vouch for malformed or foreign payloads."""
    path = tmp_path / "damaged.db"
    with EventStore(path) as store:
        seq = _request(store)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE events SET payload = ? WHERE kind = 'delivery_receipt_requested'", (payload,)
        )
    assert read_delivery_receipt(path, seq) is None


@pytest.mark.parametrize("table", ["events", "delivery_receipts"])
def test_lookalike_schema_without_a_unique_integer_key_is_unavailable(
    tmp_path: Path, table: str
) -> None:
    """A view of compatible columns is not mistaken for the writer's keyed table."""
    path = tmp_path / "lookalike.db"
    with EventStore(path) as store:
        seq = _request(store)
    with sqlite3.connect(path) as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO backing")
        connection.execute(f"CREATE VIEW {table} AS SELECT * FROM backing")
    assert read_delivery_receipt(path, seq) is None


@pytest.mark.parametrize(
    "sql,parameters",
    [
        ("UPDATE delivery_receipts SET updated_event_seq=999", ()),
        ("UPDATE delivery_receipts SET state='immediate_delivered'", ()),
        ("UPDATE delivery_receipts SET sender=''", ()),
        ("UPDATE delivery_receipts SET target=''", ()),
        ("UPDATE events SET kind='unknown' WHERE seq=2", ()),
        ("UPDATE events SET ts='broken' WHERE seq=2", ()),
        ("UPDATE events SET ts=? WHERE seq=2", (float("inf"),)),
        ("UPDATE events SET payload=? WHERE seq=2", (b"not-text",)),
    ],
)
def test_damaged_aggregate_and_event_metadata_cannot_establish_delivery(
    tmp_path: Path, sql: str, parameters: tuple[object, ...]
) -> None:
    """Broken references, forged states and malformed row fields fail closed."""
    path = tmp_path / "metadata.db"
    with EventStore(path) as store:
        seq = _request(store)
    with sqlite3.connect(path) as connection:
        connection.execute(sql, parameters)
    assert read_delivery_receipt(path, seq) is None


@pytest.mark.parametrize(
    "kind,delivered",
    [
        ("delivery_receipt_immediate", "yes"),
        ("delivery_receipt_deferred", False),
        ("delivery_receipt_expired", True),
    ],
)
def test_impossible_event_verdict_is_unavailable(
    tmp_path: Path, kind: str, delivered: object
) -> None:
    """An aggregate cannot turn a contradictory event into a definite answer."""
    path = tmp_path / "verdict.db"
    with EventStore(path) as store:
        seq = _request(store)
    with sqlite3.connect(path) as connection:
        encoded = connection.execute("SELECT payload FROM events WHERE seq=2").fetchone()[0]
        payload = json.loads(encoded)
        payload["delivered"] = delivered
        connection.execute(
            "UPDATE events SET kind=?, payload=? WHERE seq=2", (kind, json.dumps(payload))
        )
    assert read_delivery_receipt(path, seq) is None


def test_enrolled_authentication_requires_and_verifies_the_exact_event_key(tmp_path: Path) -> None:
    """No key, another key or a forged authenticated state can vouch for delivery."""
    path = tmp_path / "authenticated.db"
    key = RowMacKey(b"a" * 32, 0)
    with EventStore(path) as store:
        store.enable_row_mac(key)
        seq = _request(store)
        assert read_delivery_receipt(path, seq) is None
        view = read_delivery_receipt(path, seq, row_mac=key)
        assert view is not None and view.state == "requested"
        assert read_delivery_receipt(path, seq, row_mac=RowMacKey(b"b" * 32, 0)) is None
        _transition(store, seq, "immediate_delivered")
        delivered = read_delivery_receipt(path, seq, row_mac=key)
        assert delivered is not None and delivered.state == "immediate_delivered"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE delivery_receipts SET state='expired'")
    assert read_delivery_receipt(path, seq, row_mac=key) is None
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE delivery_receipts SET state='immediate_delivered'")
        connection.execute("UPDATE events SET mac=NULL WHERE seq=3")
    assert read_delivery_receipt(path, seq, row_mac=key) is None
    assert read_delivery_receipt(path, seq) is None


def test_legacy_prefix_and_pending_enrollment_are_not_authenticated(tmp_path: Path) -> None:
    """Supplying a key cannot vouch for an un-MACed event predating enrollment."""
    path = tmp_path / "prefix.db"
    with EventStore(path) as store:
        seq = _request(store)
        key = RowMacKey(b"k" * 32, store.max_seq())
        store.enable_row_mac(key)
        assert read_delivery_receipt(path, seq, row_mac=key) is None
        row_mac_key_path(path).write_text("enrollment marker")
        assert read_delivery_receipt(path, seq) is None


@pytest.mark.parametrize("key_input", ["short", "absent-file"])
def test_requested_encryption_has_a_named_safe_failure(tmp_path: Path, key_input: str) -> None:
    """Invalid explicit encryption setup uses the frozen projection error."""
    path = tmp_path / "key.db"
    with EventStore(path) as store:
        seq = _request(store)
    with pytest.raises(ReceiptProjectionError) as caught:
        if key_input == "short":
            read_delivery_receipt(path, seq, key=b"short")
        else:
            read_delivery_receipt(path, seq, key_file=tmp_path / "absent.key")
    assert caught.value.code == "receipt_projection"
    assert str(tmp_path) not in str(caught.value)
    assert not (tmp_path / "absent.key").exists()


def test_real_sqlcipher_receipt_lookup_is_read_only(tmp_path: Path) -> None:
    """Use actual page encryption, including the live encrypted WAL and key file."""
    from synapse_channel.core.at_rest import generate_key_file, load_key_file
    from synapse_channel.core.persistence_sqlcipher import sqlcipher_available

    path = tmp_path / "encrypted.db"
    key_file = tmp_path / "encrypted.key"
    generate_key_file(key_file)
    if not sqlcipher_available():
        with EventStore(path) as store:
            seq = _request(store)
        with pytest.raises(ReceiptProjectionError):
            read_delivery_receipt(path, seq, key_file=key_file)
        return
    with EventStore(path, key_file=key_file) as store:
        seq = _request(store)
        assert read_delivery_receipt(path, seq) is None
        view = read_delivery_receipt(path, seq, key_file=key_file)
        assert view is not None and view.state == "requested"
        assert (
            read_delivery_receipt(
                path, seq, key=load_key_file(key_file), key_file=tmp_path / "unused.key"
            )
            == view
        )
        _transition(store, seq, "immediate_delivered")
        latest = read_delivery_receipt(path, seq, key_file=key_file)
        assert latest is not None and latest.state == "immediate_delivered"
        with pytest.raises(ReceiptProjectionError):
            read_delivery_receipt(path, seq, key=b"wrong" * 6 + b"xx")
    before = path.read_bytes()
    assert read_delivery_receipt(path, seq, key_file=key_file) is not None
    assert path.read_bytes() == before
