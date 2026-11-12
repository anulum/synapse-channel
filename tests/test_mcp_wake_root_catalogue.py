# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake root catalogue contracts
"""Exercise real catalogue corruption, capacity and fenced archive transitions."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.mcp.wake_limits import MAX_RETIRED_GENERATIONS, ROOT_CATALOGUE
from synapse_channel.mcp.wake_pointer import WakeLedgerError
from synapse_channel.mcp.wake_root import locked_wake_root
from synapse_channel.mcp.wake_root_archive import snapshot_wake_generation
from synapse_channel.mcp.wake_root_catalogue import RootEntry, root_catalogue
from test_mcp_wake_root import ledger
from test_mcp_wake_root_archive import archive, inactive_generation


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM meta",
        "UPDATE meta SET revision=-1",
        "UPDATE meta SET revision='invalid'",
        "UPDATE entries SET phase='unknown'",
        "UPDATE entries SET scope_key='invalid'",
        "PRAGMA application_id=1",
        "PRAGMA user_version=3",
        "UPDATE entries SET archive_path='" + "x" * 4097 + "'",
        "UPDATE entries SET manifest='" + "x" * 8193 + "'",
        "UPDATE entries SET retired_at=-1",
        "UPDATE entries SET fingerprint='invalid'",
        "UPDATE entries SET location_digest='invalid'",
        "UPDATE meta SET policy_name='../another.json'",
        "UPDATE meta SET policy_name=x'00'",
    ],
    ids=[
        "missing-meta",
        "negative-revision",
        "typed-revision",
        "phase",
        "key",
        "application",
        "schema",
        "path-budget",
        "manifest-budget",
        "time",
        "fingerprint",
        "location",
        "policy-path",
        "policy-type",
    ],
)
def test_corrupt_catalogue_refuses_before_native_pending_work_changes(
    tmp_path: Path, statement: str
) -> None:
    entry = ledger(tmp_path / "state")
    entry.observe_page(0, 7, sources=("messages",))
    before = entry.path.read_bytes()
    with closing(sqlite3.connect(entry.path.parent / ROOT_CATALOGUE)) as database, database:
        database.execute(statement)
    with pytest.raises(WakeLedgerError):
        entry.observe_page(7, 8, sources=("tasks",))
    assert entry.path.read_bytes() == before


@pytest.mark.parametrize("phase,count", [("active", 1025), ("retired", 4097)])
def test_corrupt_catalogue_population_refuses_without_forgetting_native_state(
    tmp_path: Path, phase: str, count: int
) -> None:
    entry = ledger(tmp_path / "state")
    entry.observe_page(0, 1, sources=("messages",))
    before = entry.path.read_bytes()
    with closing(sqlite3.connect(entry.path.parent / ROOT_CATALOGUE)) as database, database:
        database.execute("DELETE FROM entries")
        database.executemany(
            "INSERT INTO entries(scope_key,phase) VALUES(?,?)",
            [(f"{number:064x}", phase) for number in range(count)],
        )
    with pytest.raises(WakeLedgerError, match="catalogue capacity exceeded"):
        entry.summary()
    assert entry.path.read_bytes() == before
    with closing(sqlite3.connect(entry.path.parent / ROOT_CATALOGUE)) as database, database:
        assert database.execute("SELECT count(*) FROM entries").fetchone()[0] == count


def test_retirement_fence_exhaustion_preserves_failed_delivery_and_all_prior_fences(
    tmp_path: Path,
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    with closing(sqlite3.connect(old.path.parent / ROOT_CATALOGUE)) as database, database:
        database.executemany(
            "INSERT INTO entries(scope_key,phase) VALUES(?,'retired')",
            [(f"{number:064x}",) for number in range(MAX_RETIRED_GENERATIONS)],
        )
    with pytest.raises(WakeLedgerError, match="retirement fence capacity"):
        archive(control, info, tmp_path / "archive")
    assert old.path.read_bytes() == before
    assert control.status()["phases"] == {"active": 1, "retired": MAX_RETIRED_GENERATIONS}
    assert control.inspect(old.scope.key)["transport"] == {
        "pending_sources": 0,
        "active_deliveries": 0,
        "unresolved_failures": 1,
    }


def test_archiving_a_legacy_unregistered_generation_preserves_its_raw_failure(
    tmp_path: Path,
) -> None:
    _, old, control, _ = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    with closing(sqlite3.connect(old.path.parent / ROOT_CATALOGUE)) as database, database:
        database.execute("DELETE FROM entries WHERE scope_key=?", (old.scope.key,))
    info = control.inspect(old.scope.key)
    assert info["phase"] == "legacy"
    result = archive(control, info, tmp_path / "archive")
    assert result["outcome"] == "archived" and not old.path.exists()
    assert (Path(result["archive_directory"]) / old.path.name).read_bytes() == before
    assert control.inspect(old.scope.key)["phase"] == "retired"


def test_catalogue_rejects_unreserved_activation_and_invalid_generation_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    before = entry.path.read_bytes()
    with locked_wake_root(root) as inventory:
        with root_catalogue(root) as catalogue:
            revision = catalogue.revision()
            with pytest.raises(WakeLedgerError, match="invalid wake root generation"):
                catalogue.reserve(inventory, "../outside")
            with pytest.raises(WakeLedgerError, match="reservation changed"):
                catalogue.activate("f" * 64)
            assert catalogue.revision() == revision
    assert entry.path.read_bytes() == before and entry.scan_cursor() == 1


def test_repeated_active_reservation_and_activation_do_not_advance_the_root_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    before = entry.path.read_bytes()
    with locked_wake_root(root) as inventory:
        with root_catalogue(root) as catalogue:
            revision = catalogue.revision()
            assert catalogue.reserve(inventory, entry.scope.key) is False
            catalogue.activate(entry.scope.key)
            assert catalogue.revision() == revision
    assert entry.path.read_bytes() == before and entry.scan_cursor() == 1


def test_archive_transition_fences_preserve_the_committed_intent_and_original_sources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    before = entry.path.read_bytes()
    with locked_wake_root(root):
        snapshot = snapshot_wake_generation(root, entry.scope.key)
        intent = RootEntry(
            entry.scope.key,
            "archiving",
            snapshot.fingerprint,
            str(tmp_path / "archive"),
            snapshot.encode(),
            "1" * 64,
        )
        with root_catalogue(root) as catalogue:
            revision = catalogue.revision()
            with pytest.raises(WakeLedgerError, match="invalid wake archive phase"):
                catalogue.begin_archive(replace(intent, manifest=""))
            for timestamp in (-1, True):
                with pytest.raises(WakeLedgerError, match="invalid wake archive timestamp"):
                    catalogue.finish_archive(intent.key, intent.fingerprint, "1" * 64, timestamp)
            with pytest.raises(WakeLedgerError, match="intent changed"):
                catalogue.finish_archive(intent.key, intent.fingerprint, "1" * 64, 100)
            with pytest.raises(WakeLedgerError, match="intent changed"):
                catalogue.cancel_archive(intent.key, intent.fingerprint)
            assert catalogue.revision() == revision
            catalogue.begin_archive(intent)
        with root_catalogue(root) as catalogue:
            revision = catalogue.revision()
            with pytest.raises(WakeLedgerError, match="intent changed"):
                catalogue.begin_archive(replace(intent, location_digest="2" * 64))
            with pytest.raises(WakeLedgerError, match="intent changed"):
                catalogue.finish_archive(intent.key, intent.fingerprint, "2" * 64, 100)
            with pytest.raises(WakeLedgerError, match="intent changed"):
                catalogue.cancel_archive(intent.key, "2" * 64)
            assert catalogue.get(intent.key) == intent and catalogue.revision() == revision
            catalogue.cancel_archive(intent.key, intent.fingerprint)
    assert entry.path.read_bytes() == before and entry.summary()["pending_sources"] == 1


def test_policy_binding_accepts_physical_alias_but_never_a_replacement_or_missing_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    policy, alias = tmp_path / "first.json", tmp_path / "alias.json"
    policy.write_text("{}")
    os.link(policy, alias)
    before = entry.path.read_bytes()
    with locked_wake_root(root):
        with root_catalogue(root) as catalogue:
            revision = catalogue.revision()
            catalogue.bind_policy(policy.name)
            catalogue.bind_policy(alias.name)
            assert catalogue.revision() == revision
    alias.unlink()
    alias.write_text("{}")
    with locked_wake_root(root):
        with root_catalogue(root) as catalogue:
            with pytest.raises(WakeLedgerError, match="another policy"):
                catalogue.bind_policy(alias.name)
            policy.unlink()
            with pytest.raises(WakeLedgerError, match="another policy"):
                catalogue.bind_policy(alias.name)
            with pytest.raises(WakeLedgerError, match="name is required"):
                catalogue.bind_policy("")
    assert entry.path.read_bytes() == before and entry.scan_cursor() == 1


@pytest.mark.parametrize("retired", [False, True])
def test_catalogue_schema_one_migration_preserves_native_work_and_retirement_fences(
    tmp_path: Path,
    retired: bool,
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    if retired:
        archive(control, info, tmp_path / "archive")
    expected = control.status()
    native = old.path.read_bytes() if not retired else None
    database_path = old.path.parent / ROOT_CATALOGUE
    with closing(sqlite3.connect(database_path)) as database, database:
        entries = database.execute("SELECT * FROM entries ORDER BY scope_key").fetchall()
        database.execute("ALTER TABLE meta RENAME TO old_meta")
        database.execute(
            "CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,"
            "max_ledgers INTEGER NOT NULL,max_bytes INTEGER NOT NULL)"
        )
        database.execute("INSERT INTO meta SELECT id,revision,max_ledgers,max_bytes FROM old_meta")
        database.execute("DROP TABLE old_meta")
        database.execute("PRAGMA user_version=1")
    observed = control.status()
    assert observed.pop("logical_bytes") == sum(
        path.stat().st_size for path in old.path.parent.iterdir()
    )
    expected.pop("logical_bytes")
    assert observed == expected
    with closing(sqlite3.connect(database_path)) as database, database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 2
        assert database.execute("SELECT * FROM entries ORDER BY scope_key").fetchall() == entries
        assert database.execute("SELECT policy_name FROM meta").fetchone()[0] == "grants.json"
    if retired:
        with pytest.raises(WakeLedgerError, match="archived"):
            old.summary()
    else:
        assert old.path.read_bytes() == native
        assert old.summary()["unresolved_failures"] == 1
