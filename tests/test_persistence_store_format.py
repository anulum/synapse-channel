# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — event-store format floor tests
"""The event log records its store format, and every opener refuses a newer one unchanged."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import free_port
from home_redirect import redirect_home
from synapse_channel import __version__
from synapse_channel.cli_hub_startup import NO_RESTART_EXIT_CODE
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    connect_event_store,
    connect_sqlcipher,
    migrate_plaintext_to_sqlcipher,
    sqlcipher_available,
)
from synapse_channel.core.persistence_store_format import (
    STORE_FORMAT,
    StoreFormatRefused,
    record_store_format,
)
from synapse_channel.service_templates import render_hub_unit

requires_sqlcipher = pytest.mark.skipif(
    not sqlcipher_available(), reason="sqlcipher3-binary not installed"
)

_ROOT = Path(__file__).resolve().parents[1]


def _record(connection: sqlite3.Connection) -> tuple[int, str, str]:
    """Return the header floor and the release names of the store-format record."""
    floor = int(connection.execute("PRAGMA user_version").fetchone()[0])
    row = connection.execute(
        "SELECT floor, floor_release, last_writer_release FROM store_format WHERE id = 1"
    ).fetchone()
    assert row[0] == floor
    return floor, str(row[1]), str(row[2])


def _plain_record(path: Path) -> tuple[int, str, str]:
    with sqlite3.connect(path) as connection:
        return _record(connection)


def _rewrite_plain(path: Path, *statements: str) -> None:
    """Apply what another release would have written, then close the connection."""
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def _future_store(path: Path, *, release: str | None = "9.0.0") -> None:
    """Leave a real store in the state a release with a newer format leaves it."""
    EventStore(path).close()
    statements = [f"PRAGMA user_version = {STORE_FORMAT + 1}"]
    if release is None:
        statements.insert(0, "DROP TABLE store_format")
    else:
        statements.insert(
            0,
            f"UPDATE store_format SET floor = {STORE_FORMAT + 1}, floor_release = '{release}', "
            f"last_writer_release = '{release}' WHERE id = 1",
        )
    _rewrite_plain(path, *statements)


def _snapshot(path: Path) -> tuple[str, list[str]]:
    """Digest the store file and list its directory, the evidence that nothing changed."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, sorted(entry.name for entry in path.parent.iterdir())


def test_a_new_log_records_this_release_and_its_format(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        store.append("chat", {"text": "first"})
    assert _plain_record(db) == (STORE_FORMAT, __version__, __version__)


def test_a_log_from_before_the_floor_is_raised_once_and_keeps_its_events(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    with EventStore(db) as store:
        store.append("chat", {"text": "kept"})
    _rewrite_plain(db, "DROP TABLE store_format", "PRAGMA user_version = 0")

    with EventStore(db) as store:
        texts = [event.payload["text"] for event in store.read_all()]
    assert texts == ["kept"]
    assert _plain_record(db) == (STORE_FORMAT, __version__, __version__)


def test_a_later_release_keeps_the_raiser_named_and_records_itself_as_the_writer(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()
    _rewrite_plain(
        db,
        "UPDATE store_format SET floor_release = '0.0.1', last_writer_release = '0.0.2' "
        "WHERE id = 1",
    )

    EventStore(db).close()

    assert _plain_record(db) == (STORE_FORMAT, "0.0.1", __version__)


def test_a_reopen_by_the_same_release_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()
    before = _snapshot(db)

    connection, _encrypted = connect_event_store(db)
    try:
        changes = connection.total_changes
        assert record_store_format(connection, release=__version__) == STORE_FORMAT
        assert connection.total_changes == changes
        assert not connection.in_transaction
    finally:
        connection.close()

    assert _snapshot(db) == before


@pytest.mark.parametrize("release", ["9.0.0", None])
def test_the_writer_refuses_a_newer_format_and_leaves_the_file_as_it_was(
    tmp_path: Path, release: str | None
) -> None:
    db = tmp_path / "hub.db"
    _future_store(db, release=release)
    before = _snapshot(db)

    with pytest.raises(StoreFormatRefused) as refused:
        EventStore(db)

    message = str(refused.value)
    assert str(db) in message
    assert f"needs store format {STORE_FORMAT + 1}" in message
    assert ("release 9.0.0" if release else "an unnamed release") in message
    assert f"release {__version__} reads store formats up to {STORE_FORMAT}" in message
    assert (refused.value.floor, refused.value.floor_release) == (STORE_FORMAT + 1, release)
    assert _snapshot(db) == before


def test_a_reader_refuses_a_newer_format_before_it_reads_anything(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _future_store(db)
    before = _snapshot(db)

    with pytest.raises(StoreFormatRefused):
        connect_event_store(db)

    assert _snapshot(db) == before


def test_a_negative_header_value_is_refused_as_a_damaged_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    EventStore(db).close()
    _rewrite_plain(db, "PRAGMA user_version = -1")
    before = _snapshot(db)

    with pytest.raises(ValueError, match="event store format floor -1 is negative") as refused:
        connect_event_store(db)

    assert not isinstance(refused.value, StoreFormatRefused)
    assert _snapshot(db) == before


def _hub_environment(home: Path) -> dict[str, str]:
    environment = redirect_home(os.environ, home)
    environment["XDG_DATA_HOME"] = str(home / "data")
    environment["PYTHONPATH"] = os.pathsep.join((str(_ROOT / "src"), str(_ROOT / "tests")))
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_hub(home: Path, db: Path) -> subprocess.CompletedProcess[str]:
    """Run the real hub command on ``db`` in an owned profile with a bounded lifetime."""
    return subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "hub", "--db", str(db)]
        + ["--port", str(free_port())],
        cwd=home,
        env=_hub_environment(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_hub_exits_without_a_restart_status_and_changes_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    db = home / "journal" / "hub.db"
    db.parent.mkdir()
    _future_store(db)
    before = _snapshot(db)

    result = _run_hub(home, db)

    assert result.returncode == NO_RESTART_EXIT_CODE == 78, result.stderr
    assert f"synapse hub: event store {db} needs store format {STORE_FORMAT + 1}" in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(db) == before


def test_the_hub_unit_does_not_restart_on_the_refusal_status() -> None:
    rendered = render_hub_unit(synapse_bin="/usr/bin/synapse")
    shipped = (_ROOT / "deploy" / "synapse-hub.service").read_text(encoding="utf-8")
    for unit in (rendered, shipped):
        service = unit.split("[Service]", 1)[1].split("[Install]", 1)[0]
        assert f"RestartPreventExitStatus={NO_RESTART_EXIT_CODE}" in service.splitlines()


@requires_sqlcipher
def test_an_encrypted_log_without_its_key_keeps_the_ordinary_refusal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    db = home / "journal" / "hub.db"
    db.parent.mkdir()
    EventStore(db, key_file=generate_key_file(tmp_path / "hub.key")).close()
    before = _snapshot(db)

    result = _run_hub(home, db)

    assert result.returncode == 2, result.stderr
    assert f"cannot open {db} as a plaintext SQLite database" in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(db)[0] == before[0]


@requires_sqlcipher
def test_an_encrypted_log_records_and_refuses_the_same_way(tmp_path: Path) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    EventStore(db, key_file=key).close()
    material = key.read_bytes()
    connection = connect_sqlcipher(db, material)
    try:
        assert _record(connection) == (STORE_FORMAT, __version__, __version__)
        connection.execute(f"PRAGMA user_version = {STORE_FORMAT + 1}")
        connection.commit()
    finally:
        connection.close()
    before = _snapshot(db)

    with pytest.raises(StoreFormatRefused):
        connect_event_store(db, key_file=key)
    with pytest.raises(StoreFormatRefused):
        EventStore(db, key_file=key)

    assert _snapshot(db) == before


@requires_sqlcipher
def test_the_conversion_carries_the_format_and_refuses_a_newer_source(tmp_path: Path) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    source = tmp_path / "plain.db"
    with EventStore(source) as store:
        store.append("chat", {"text": "carried"})
    destination = tmp_path / "encrypted.db"

    assert migrate_plaintext_to_sqlcipher(source, destination, key_file=key) == {"rows": 1}

    connection = connect_sqlcipher(destination, key.read_bytes())
    try:
        assert _record(connection) == (STORE_FORMAT, __version__, __version__)
    finally:
        connection.close()
    with EventStore(destination, key_file=key) as store:
        assert [event.payload["text"] for event in store.read_all()] == ["carried"]

    newer = tmp_path / "newer.db"
    _future_store(newer)
    refused_destination = tmp_path / "refused.db"
    with pytest.raises(StoreFormatRefused):
        migrate_plaintext_to_sqlcipher(newer, refused_destination, key_file=key)
    assert not refused_destination.exists()
