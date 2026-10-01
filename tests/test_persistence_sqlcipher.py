# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher live event-store tests

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import KEY_BYTES, generate_key_file, load_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    SqlCipherKeyError,
    connect_sqlcipher,
    import_sqlcipher_module,
    migrate_plaintext_to_sqlcipher,
    pragma_key_literal,
    rekey_sqlcipher_store,
    sqlcipher_available,
)

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed (pip install synapse-channel[sqlcipher])",
)


def test_encrypted_failed_schema_open_closes_handle_and_allows_recovery(tmp_path: Path) -> None:
    """Real SQLCipher failures release WAL ownership despite retained tracebacks."""
    path = tmp_path / "incompatible.db"
    key = os.urandom(KEY_BYTES)
    connection = connect_sqlcipher(path, key)
    try:
        connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO events VALUES (7)")
        connection.commit()
    finally:
        connection.close()
    failures: list[BaseException] = []
    for _ in range(3):
        with pytest.raises(import_sqlcipher_module().OperationalError) as failure:
            EventStore(path, key=key)
        failures.append(failure.value)
        assert failures[-1].__traceback__ is not None
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()
    connection = connect_sqlcipher(path, key)
    try:
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN IMMEDIATE")
        assert connection.execute("SELECT seq FROM events").fetchall() == [(7,)]
        connection.execute("ALTER TABLE events ADD COLUMN ts REAL NOT NULL DEFAULT 1")
        connection.execute("ALTER TABLE events ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
        connection.execute("ALTER TABLE events ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    finally:
        connection.close()
    with EventStore(path, key=key) as recovered:
        assert recovered.encrypted
        assert recovered.read_all()[0].seq == 7
        assert recovered.append("chat", {"recovered": True}) == 8
    with EventStore(path, key=key) as reopened:
        assert reopened.read_all()[1].payload == {"recovered": True}


@pytest.mark.parametrize("failure_type", [RuntimeError, BaseException])
def test_encrypted_interrupted_open_closes_handle(
    tmp_path: Path, failure_type: type[BaseException]
) -> None:
    """Encrypted initialization closes on ordinary and process-level interruption."""
    path = tmp_path / "interrupted.db"
    key = os.urandom(KEY_BYTES)
    with EventStore(path, key=key) as initial:
        initial.append("chat", {"marker": "kept"})
    interruption = failure_type("caller interrupted encrypted initialization")

    def interrupted_kinds() -> Iterator[str]:
        """Interrupt the supplied public iterable after its first kind."""
        yield "chat"
        raise interruption

    with pytest.raises(failure_type) as failure:
        EventStore(path, key=key, aef_outbox_kinds=interrupted_kinds())
    assert failure.value is interruption
    assert interruption.__traceback__ is not None
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    with EventStore(path, key=key) as recovered:
        assert recovered.read_all()[0].payload == {"marker": "kept"}
        assert recovered.append("chat", {"recovered": True}) == 2


def test_pragma_key_literal_is_hex_form() -> None:
    key = b"\x00" * KEY_BYTES
    assert pragma_key_literal(key) == "x'" + ("00" * KEY_BYTES) + "'"
    with pytest.raises(ValueError, match="32 bytes"):
        pragma_key_literal(b"short")


def test_encrypted_event_store_round_trip(tmp_path: Path) -> None:
    key_path = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key_path)
    assert store.encrypted is True
    seq = store.append("chat", {"text": "secret body"})
    assert seq == 1
    events = store.read_all()
    assert events[0].payload == {"text": "secret body"}
    store.close()

    reopened = EventStore(db, key_file=key_path)
    assert reopened.read_all()[0].payload["text"] == "secret body"
    reopened.close()


def test_wrong_key_is_rejected(tmp_path: Path) -> None:
    key_a = generate_key_file(tmp_path / "a.key")
    key_b = generate_key_file(tmp_path / "b.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key_a)
    store.append("chat", {"text": "x"})
    store.close()
    with pytest.raises(SqlCipherKeyError):
        EventStore(db, key_file=key_b)


def test_plaintext_bytes_do_not_contain_payload(tmp_path: Path) -> None:
    """Ciphertext file must not contain the raw JSON body in the clear."""
    key_path = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    secret = "unique-payload-marker-9f3c2a"
    store = EventStore(db, key_file=key_path)
    store.append("chat", {"text": secret})
    store.close()
    raw = db.read_bytes()
    assert secret.encode() not in raw
    assert b"unique-payload-marker" not in raw


def test_migrate_plaintext_to_sqlcipher_preserves_seqs(tmp_path: Path) -> None:
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    key_path = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.append("claim", {"n": 2})
    source.close()

    result = migrate_plaintext_to_sqlcipher(plain, enc, key_file=key_path)
    assert result["rows"] == 2

    migrated = EventStore(enc, key_file=key_path)
    events = migrated.read_all()
    assert [e.seq for e in events] == [1, 2]
    assert events[0].payload == {"n": 1}
    assert events[1].kind == "claim"
    migrated.close()


def test_migrate_refuses_existing_destination(tmp_path: Path) -> None:
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    key_path = generate_key_file(tmp_path / "hub.key")
    EventStore(plain).close()
    enc.write_bytes(b"exists")
    with pytest.raises(FileExistsError):
        migrate_plaintext_to_sqlcipher(plain, enc, key_file=key_path)


def test_load_key_file_matches_generate(tmp_path: Path) -> None:
    path = generate_key_file(tmp_path / "k.key")
    material = load_key_file(path)
    assert len(material) == KEY_BYTES
    assert material == path.read_bytes()


def test_stock_event_store_stays_unencrypted(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "plain.db")
    assert store.encrypted is False
    store.append("chat", {"text": "visible"})
    store.close()
    assert b"visible" in (tmp_path / "plain.db").read_bytes()


def test_plaintext_open_of_encrypted_store_hints_key_file(tmp_path: Path) -> None:
    key_path = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key_path)
    store.append("chat", {"text": "secret"})
    store.close()
    with pytest.raises(SqlCipherKeyError, match="--db-key-file"):
        EventStore(db)


def test_rekey_sqlcipher_store_rotates_key(tmp_path: Path) -> None:
    """PRAGMA rekey: old key fails closed; new key reads prior events."""
    old = generate_key_file(tmp_path / "old.key")
    new = generate_key_file(tmp_path / "new.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=old)
    store.append("chat", {"text": "before-rekey"})
    store.close()

    result = rekey_sqlcipher_store(db, old_key_file=old, new_key_file=new)
    assert result["status"] == "rekeyed"

    with pytest.raises(SqlCipherKeyError):
        EventStore(db, key_file=old)
    reopened = EventStore(db, key_file=new)
    assert reopened.read_all()[0].payload == {"text": "before-rekey"}
    reopened.close()


def test_rekey_sqlcipher_refuses_identical_keys(tmp_path: Path) -> None:
    key = generate_key_file(tmp_path / "k.key")
    db = tmp_path / "hub.db"
    EventStore(db, key_file=key).close()
    with pytest.raises(ValueError, match="must differ"):
        rekey_sqlcipher_store(db, old_key_file=key, new_key_file=key)
