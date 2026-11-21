# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — declared store migration tests
"""A declared migration applies whole from its base, or leaves the store exactly as it was."""

from __future__ import annotations

import dataclasses
import hashlib
import multiprocessing
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.sqlite_schema import schema_digest
from synapse_channel.core.store_migration import (
    ATTEMPTS_TABLE,
    MIGRATIONS_TABLE,
    DeclaredMigration,
    MigrationRefused,
    apply_declared_migration,
)
from synapse_channel.result_verification_effects import verify_migration

_BASE_LAYOUT = (
    "CREATE TABLE readings (id INTEGER PRIMARY KEY, sensor TEXT NOT NULL, value REAL NOT NULL)",
)
_ADD_UNIT = "ALTER TABLE readings ADD COLUMN unit TEXT NOT NULL DEFAULT 'kelvin'"
_UNIQUE_SENSOR = "CREATE UNIQUE INDEX readings_sensor ON readings(sensor)"


def _store(path: Path, *rows: tuple[str, float]) -> str:
    """Create a real store at version 1 with ``rows`` and return its schema digest."""
    with closing(sqlite3.connect(path)) as connection:
        for statement in _BASE_LAYOUT:
            connection.execute(statement)
        connection.executemany("INSERT INTO readings (sensor, value) VALUES (?, ?)", rows)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        return schema_digest(connection)


def _produced_digest(base: Path, *statements: str) -> str:
    """Return the digest a migration of a copy of ``base`` produces, bookkeeping included."""
    with closing(sqlite3.connect(":memory:")) as scratch, closing(sqlite3.connect(base)) as source:
        source.backup(scratch)
        migration = DeclaredMigration("probe", schema_digest(scratch), statements, "0" * 64, 2)
        with pytest.raises(MigrationRefused) as refused:
            apply_declared_migration(scratch, migration, attempt="probe")
        return refused.value.observed_schema_sha256


def _fingerprint(path: Path) -> tuple[str, int]:
    with closing(sqlite3.connect(path)) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    return hashlib.sha256(path.read_bytes()).hexdigest(), version


def _apply(path: Path, migration: DeclaredMigration, attempt: str) -> str:
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        return apply_declared_migration(connection, migration, attempt=attempt)


def test_a_declared_migration_applies_once_and_the_verifier_confirms_it(tmp_path: Path) -> None:
    store = tmp_path / "readings.sqlite3"
    base = _store(store, ("north", 1.5))
    migration = DeclaredMigration(
        "readings-2", base, (_ADD_UNIT,), _produced_digest(store, _ADD_UNIT), 2
    )

    assert _apply(store, migration, "first") == "applied"
    assert _apply(store, migration, "second") == "already_applied"

    with closing(sqlite3.connect(store)) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert connection.execute(f"SELECT version FROM {MIGRATIONS_TABLE}").fetchall() == [
            ("readings-2",)
        ]
        assert connection.execute(f"SELECT * FROM {ATTEMPTS_TABLE}").fetchall() == [
            ("first", "readings-2", "applied")
        ]
        assert connection.execute("SELECT sensor, unit FROM readings").fetchall() == [
            ("north", "kelvin")
        ]
    verdict = verify_migration(store, "readings-2", migration.schema_sha256, operation_id="op-1")
    assert verdict.result == "verified"


def test_a_store_that_is_not_the_declared_base_is_refused_unchanged(tmp_path: Path) -> None:
    store = tmp_path / "readings.sqlite3"
    base = _store(store, ("north", 1.5))
    migration = DeclaredMigration(
        "readings-2", base, (_ADD_UNIT,), _produced_digest(store, _ADD_UNIT), 2
    )
    with closing(sqlite3.connect(store)) as connection:
        connection.execute("CREATE INDEX readings_by_value ON readings(value)")
        connection.commit()
        changed = schema_digest(connection)
    before = _fingerprint(store)

    with pytest.raises(MigrationRefused) as refused:
        _apply(store, migration, "first")

    assert (refused.value.reason, refused.value.observed_schema_sha256) == (
        "base_schema_differs",
        changed,
    )
    assert "migration readings-2 refused (base_schema_differs)" in str(refused.value)
    assert _fingerprint(store) == before


def test_a_failing_statement_rolls_back_the_marker_and_every_earlier_change(
    tmp_path: Path,
) -> None:
    store = tmp_path / "readings.sqlite3"
    base = _store(store, ("north", 1.5), ("north", 2.5))
    migration = DeclaredMigration("readings-2", base, (_ADD_UNIT, _UNIQUE_SENSOR), "0" * 64, 2)
    before = _fingerprint(store)

    with pytest.raises(sqlite3.IntegrityError):
        _apply(store, migration, "first")

    assert _fingerprint(store) == before
    with closing(sqlite3.connect(store)) as connection:
        assert schema_digest(connection) == base


def test_a_wrong_produced_digest_is_refused_and_nothing_is_kept(tmp_path: Path) -> None:
    store = tmp_path / "readings.sqlite3"
    base = _store(store, ("north", 1.5))
    expected = _produced_digest(store, _ADD_UNIT)
    migration = DeclaredMigration("readings-2", base, (_ADD_UNIT,), "f" * 64, 2)
    before = _fingerprint(store)

    with pytest.raises(MigrationRefused) as refused:
        _apply(store, migration, "first")

    assert (refused.value.reason, refused.value.observed_schema_sha256) == (
        "produced_schema_differs",
        expected,
    )
    assert _fingerprint(store) == before


def _racer(
    path: str,
    migration: DeclaredMigration,
    attempt: str,
    barrier: threading.Barrier,
    outcomes: multiprocessing.SimpleQueue[str],
) -> None:
    barrier.wait()
    outcomes.put(_apply(Path(path), migration, attempt))


def test_concurrent_openers_migrate_a_store_exactly_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    for round_number in range(5):
        store = tmp_path / f"readings-{round_number}.sqlite3"
        base = _store(store, ("north", 1.5))
        migration = DeclaredMigration(
            "readings-2", base, (_ADD_UNIT,), _produced_digest(store, _ADD_UNIT), 2
        )
        barrier = context.Barrier(2)
        queue: multiprocessing.SimpleQueue[str] = context.SimpleQueue()
        openers = [
            context.Process(
                target=_racer, args=(str(store), migration, f"opener-{index}", barrier, queue)
            )
            for index in range(2)
        ]
        for opener in openers:
            opener.start()
        for opener in openers:
            opener.join(timeout=60)
        assert [opener.exitcode for opener in openers] == [0, 0]
        outcomes = sorted(queue.get() for _ in openers)
        queue.close()
        assert outcomes == ["already_applied", "applied"]
        with closing(sqlite3.connect(store)) as connection:
            assert connection.execute(f"SELECT count(*) FROM {ATTEMPTS_TABLE}").fetchone() == (1,)
            assert schema_digest(connection) == migration.schema_sha256


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"identity": "two words"}, "plain token"),
        ({"base_schema_sha256": "A" * 64}, "64 lower-case hex digits"),
        ({"schema_sha256": "0" * 63}, "64 lower-case hex digits"),
        ({"statements": ()}, "at least one statement"),
        ({"user_version": 0}, "positive layout version"),
    ],
)
def test_a_declaration_the_verifier_could_not_check_is_refused(
    fields: dict[str, Any], message: str
) -> None:
    valid = DeclaredMigration("readings-2", "0" * 64, (_ADD_UNIT,), "1" * 64, 2)
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(valid, **fields)
