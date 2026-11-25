# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher live event-store tests

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.at_rest_format import KEY_BYTES
from synapse_channel.core.at_rest_keys import generate_key_file, load_key_file
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
from synapse_channel.core.persistence_store_format import forget_copied_journal_instance

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


def test_rekey_refuses_a_missing_store(tmp_path: Path) -> None:
    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError, match="not found"):
        rekey_sqlcipher_store(missing, old_key=os.urandom(KEY_BYTES), new_key=os.urandom(KEY_BYTES))


def test_rekey_reraises_a_rejected_new_key(tmp_path: Path) -> None:
    key = os.urandom(KEY_BYTES)
    db = tmp_path / "hub.db"
    EventStore(db, key=key).close()
    with pytest.raises(ValueError, match="^SQLCipher key must be") as raised:
        rekey_sqlcipher_store(db, old_key=key, new_key=b"short")
    assert type(raised.value) is ValueError


def test_rekey_wraps_an_unexpected_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.core.persistence_sqlcipher as module

    key = os.urandom(KEY_BYTES)
    db = tmp_path / "hub.db"
    EventStore(db, key=key).close()
    real_connect = module.connect_sqlcipher

    class Refusing:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def execute(self, sql: str, *args: object, **kwargs: object) -> object:
            if str(sql).startswith("PRAGMA rekey"):
                raise RuntimeError("rekey boom")
            return self._inner.execute(sql, *args, **kwargs)  # type: ignore[attr-defined]  # the wrapped driver connection has no stub

        def commit(self) -> None:
            self._inner.commit()  # type: ignore[attr-defined]  # the wrapped driver connection has no stub

        def close(self) -> None:
            self._inner.close()  # type: ignore[attr-defined]  # the wrapped driver connection has no stub

    def connect(path: Path, material: bytes) -> Refusing:
        return Refusing(real_connect(path, material))

    monkeypatch.setattr(module, "connect_sqlcipher", connect)
    with pytest.raises(SqlCipherKeyError, match="rekey failed"):
        rekey_sqlcipher_store(db, old_key=key, new_key=os.urandom(KEY_BYTES))


def test_rekey_refuses_when_the_old_key_still_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.persistence_sqlcipher as module

    key = os.urandom(KEY_BYTES)
    db = tmp_path / "hub.db"
    EventStore(db, key=key).close()
    real_connect = module.connect_sqlcipher
    opened = 0

    def connect(path: Path, material: bytes) -> object:
        nonlocal opened
        opened += 1
        if opened == 3:

            class Leftover:
                def close(self) -> None:
                    return None

            return Leftover()
        return real_connect(path, material)

    monkeypatch.setattr(module, "connect_sqlcipher", connect)
    with pytest.raises(SqlCipherKeyError, match="old key usable"):
        rekey_sqlcipher_store(db, old_key=key, new_key=os.urandom(KEY_BYTES))


def test_migrate_refuses_a_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        migrate_plaintext_to_sqlcipher(
            tmp_path / "absent.db",
            tmp_path / "enc.db",
            key=os.urandom(KEY_BYTES),
        )


def test_migrate_accepts_a_raw_key(tmp_path: Path) -> None:
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    EventStore(plain).close()
    key = os.urandom(KEY_BYTES)
    assert migrate_plaintext_to_sqlcipher(plain, enc, key=key) == {"rows": 0}
    migrated = EventStore(enc, key=key)
    assert migrated.read_all() == []
    migrated.close()


def test_owner_restriction_ignores_a_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    EventStore(plain).close()

    def refuse(_path: Path, _mode: int) -> None:
        raise OSError("chmod refused")

    monkeypatch.setattr(module.os, "chmod", refuse)  # type: ignore[attr-defined]  # the module imports this name without re-exporting it
    assert migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES)) == {"rows": 0}


def _plain_tables(path: Path) -> dict[str, int]:
    """Return every table of a plaintext store with its row count."""
    with closing(sqlite3.connect(path)) as connection:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            name: int(connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in names
        }


def test_a_migrated_store_never_reuses_a_compacted_sequence(tmp_path: Path) -> None:
    """The copy keeps the events table's ``AUTOINCREMENT``, so compaction cannot recycle a sequence.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, its encrypted copy and the key file.
    """
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    key_path = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    for n in range(3):
        source.append("chat", {"n": n})
    source.close()
    migrate_plaintext_to_sqlcipher(plain, enc, key_file=key_path)
    migrated = EventStore(enc, key_file=key_path)
    try:
        assert migrated.delete([3]) == 1
        assert migrated.append("chat", {"n": 3}) == 4
        assert [event.seq for event in migrated.read_all()] == [1, 2, 4]
    finally:
        migrated.close()


def test_a_migrated_store_holds_every_table_of_its_source(tmp_path: Path) -> None:
    """Every table of the plaintext store arrives in the copy with the same number of rows.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, its encrypted copy and the key file.
    """
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    key_path = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    source.append("chat", {"sender": "PROJ/alice", "target": "PROJ/bob", "text": "hello"})
    source.close()
    expected = _plain_tables(plain)
    migrate_plaintext_to_sqlcipher(plain, enc, key_file=key_path)
    copied = connect_sqlcipher(enc, key_path.read_bytes())
    try:
        tables = {
            str(row[0])
            for row in copied.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == set(expected)
        for name, count in expected.items():
            if name != "store_format":
                assert copied.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == count
    finally:
        copied.close()


def test_a_migrated_store_keeps_a_pending_forward(tmp_path: Path) -> None:
    """A forward still waiting in the outbox when the hub stopped is still waiting in the copy.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, its encrypted copy and the key file.
    """
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    key_path = generate_key_file(tmp_path / "hub.key")
    source = EventStore(plain)
    pending = source.message_forward.enqueue(
        forward_id="forward-1",
        peer_hub="hub-b",
        sender="PROJ/alice",
        target="hub-b:PROJ/bob",
        request={"text": "carried"},
        now=1000.0,
        expires_at=2000.0,
    )
    source.close()
    migrate_plaintext_to_sqlcipher(plain, enc, key_file=key_path)
    migrated = EventStore(enc, key_file=key_path)
    try:
        assert migrated.message_forward.outbox_entry("forward-1") == pending
        assert [entry.forward_id for entry in migrated.message_forward.due(1500.0)] == ["forward-1"]
    finally:
        migrated.close()


def _leftovers(directory: Path, name: str) -> list[str]:
    """Return every entry in ``directory`` that belongs to a conversion into ``name``."""
    return sorted(entry.name for entry in directory.iterdir() if name in entry.name)


def test_a_failed_format_step_publishes_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversion that fails after the export leaves neither a destination nor a staging file.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, the would-be copy and the key file.
    monkeypatch : pytest.MonkeyPatch
        Makes the step after the export fail, as a crash between export and format record would.
    """
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()

    def interrupted(_connection: object) -> None:
        raise RuntimeError("interrupted after the export")

    monkeypatch.setattr(module, "forget_copied_journal_instance", interrupted)
    with pytest.raises(RuntimeError, match="interrupted after the export"):
        migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES))
    assert _leftovers(tmp_path, "enc.db") == []


def test_a_failed_export_publishes_no_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversion whose export connection fails at its last step leaves nothing behind.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, the would-be copy and the key file.
    monkeypatch : pytest.MonkeyPatch
        Wraps the real SQLCipher driver so the export runs and then its detach fails.
    """
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()
    real = import_sqlcipher_module()

    class _Connection:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, sql: str, *args: object) -> Any:
            if sql.startswith("DETACH"):
                raise RuntimeError("the export connection failed")
            return self._inner.execute(sql, *args)

        def close(self) -> None:
            self._inner.close()

    class _Driver:
        def connect(self, *args: object, **kwargs: object) -> _Connection:
            return _Connection(real.connect(*args, **kwargs))

    monkeypatch.setattr(module, "import_sqlcipher_module", lambda: _Driver())
    with pytest.raises(RuntimeError, match="the export connection failed"):
        migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES))
    assert _leftovers(tmp_path, "enc.db") == []


def test_publishing_never_replaces_a_destination_that_appeared_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination created while the copy was being finished is kept, and nothing else is left.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source and the destination another writer creates.
    monkeypatch : pytest.MonkeyPatch
        Creates the destination between the existence check and the publish.
    """
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()

    def another_writer_creates_the_destination(connection: object) -> None:
        enc.write_bytes(b"another writer's file")
        forget_copied_journal_instance(connection)

    monkeypatch.setattr(
        module, "forget_copied_journal_instance", another_writer_creates_the_destination
    )
    with pytest.raises(FileExistsError):
        migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES))
    assert enc.read_bytes() == b"another writer's file"
    assert _leftovers(tmp_path, "enc.db") == ["enc.db"]


def test_a_finished_conversion_leaves_only_its_destination(tmp_path: Path) -> None:
    """A successful conversion publishes the destination and removes its staging file.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source and the published copy.
    """
    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()
    key = os.urandom(KEY_BYTES)
    assert migrate_plaintext_to_sqlcipher(plain, enc, key=key) == {"rows": 1}
    assert _leftovers(tmp_path, "enc.db") == ["enc.db"]
    migrated = EventStore(enc, key=key)
    try:
        assert [event.payload for event in migrated.read_all()] == [{"n": 1}]
    finally:
        migrated.close()


@pytest.mark.parametrize("record_fails", [False, True], ids=["after-wal", "inside-the-record"])
def test_a_failed_close_still_removes_the_staging_journal_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_fails: bool
) -> None:
    """When the copy cannot be closed, its staging file and journal files still go.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source and the would-be copy.
    monkeypatch : pytest.MonkeyPatch
        Hands the conversion a copy connection whose close fails. With
        ``record_fails`` the format record fails first, inside the write
        transaction the journal-instance clear opened, so a rollback journal
        exists beside the staging file when the close fails.
    record_fails : bool
        Whether the format record fails before the close.
    """
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()
    opened: list[Any] = []
    at_close: list[str] = []

    class _Unclosable:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, sql: str, *args: object) -> Any:
            return self._inner.execute(sql, *args)

        def commit(self) -> None:
            self._inner.commit()

        def close(self) -> None:
            at_close.extend(sorted(e.name for e in tmp_path.iterdir() if ".converting" in e.name))
            raise OSError("the copy could not be closed")

    def connect(path: Path, key: bytes) -> _Unclosable:
        connection = connect_sqlcipher(path, key)
        opened.append(connection)
        return _Unclosable(connection)

    def failing_record(_connection: object, *, release: str) -> int:
        raise RuntimeError(f"the format record for {release} failed")

    monkeypatch.setattr(module, "connect_sqlcipher", connect)
    if record_fails:
        monkeypatch.setattr(module, "record_store_format", failing_record)
    try:
        with pytest.raises(OSError, match="the copy could not be closed"):
            migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES))
        assert _leftovers(tmp_path, "enc.db") == []
    finally:
        for connection in opened:
            connection.close()
    journals = [name for name in at_close if name.endswith("-journal")]
    assert len(journals) == (1 if record_fails else 0)


def test_the_staging_file_is_owner_only_while_the_copy_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unpublished copy is readable and writable by its owner only, before any chmod.

    Parameters
    ----------
    tmp_path : Path
        Holds the plaintext source, the staging file and the published copy.
    monkeypatch : pytest.MonkeyPatch
        Reads the staging file's mode while the copy is being finished.
    """
    import synapse_channel.core.persistence_sqlcipher as module

    plain = tmp_path / "plain.db"
    enc = tmp_path / "enc.db"
    source = EventStore(plain)
    source.append("chat", {"n": 1})
    source.close()
    modes: list[int] = []

    def read_the_staging_mode(connection: object) -> None:
        modes.extend(
            entry.stat().st_mode & 0o777
            for entry in tmp_path.iterdir()
            if entry.name.endswith(".converting")
        )
        forget_copied_journal_instance(connection)

    monkeypatch.setattr(module, "forget_copied_journal_instance", read_the_staging_mode)
    migrate_plaintext_to_sqlcipher(plain, enc, key=os.urandom(KEY_BYTES))
    assert modes == [0o600]
