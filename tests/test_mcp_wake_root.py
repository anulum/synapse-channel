# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake root admission contracts
"""Exercise bounded shared-root admission, OS contention and real crash recovery."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.mcp.wake_database import wake_database
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_limits import (
    MAX_ROOT_FILES,
    MAX_WAKE_DATABASE_BYTES,
    ROOT_CATALOGUE,
    ROOT_LOCK,
    RootLimits,
)
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError, WakeScope
from synapse_channel.mcp.wake_root import locked_wake_root, scan_wake_root
from synapse_channel.mcp.wake_root_catalogue import root_catalogue
from synapse_channel.mcp.wake_sqlite import configure_wake_sqlite


def ledger(root: Path, number: int = 0) -> WakeLedger:
    """Select one synthetic grant generation, without endpoint or account material."""
    binding = hashlib.sha256(str(number).encode()).hexdigest()
    return WakeLedger(root, WakeScope("subject", "ALPHA", "ALPHA/worker", binding, "root-hub"))


@pytest.mark.parametrize(
    "name", [".", "..", "../policy.json", "dir\\policy.json", "p:stream", "p\x00", "x" * 256]
)
def test_native_scope_refuses_unsafe_policy_names_without_creating_storage(
    name: str, tmp_path: Path
) -> None:
    with pytest.raises(WakeLedgerError, match="invalid wake policy name"):
        WakeLedger(
            tmp_path / "state", WakeScope("subject", "ALPHA", "ALPHA/worker", "1" * 64, "hub", name)
        )
    assert not (tmp_path / "state").exists()


def configure(root: Path, *, slots: int = 2, max_bytes: int = 512 * 1024 * 1024) -> None:
    """Apply the real owner configuration transaction under the same OS lock."""
    with locked_wake_root(root, create=True) as inventory:
        with root_catalogue(root) as catalogue:
            catalogue.configure(RootLimits(slots, max_bytes), catalogue.revision(), inventory)


def child(root: Path, code: str) -> subprocess.CompletedProcess[str]:
    """Execute finite public storage code in an independent inheriting process."""
    return subprocess.run(
        [sys.executable, "-c", code, str(root)],
        capture_output=True,
        text=True,
        timeout=10,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        check=False,
    )


def test_root_slot_exhaustion_preserves_pending_work_and_rejects_only_new_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    configure(root)
    first, second, denied = (ledger(root, n) for n in range(3))
    assert first.observe_page(0, 4, sources=("messages",))
    assert second.observe_page(0, 2, sources=("tasks",))
    with pytest.raises(WakeLedgerError, match="generation capacity"):
        denied.observe_page(0, 1, sources=("messages",))
    assert not denied.path.exists()
    assert first.scan_cursor() == 4 and first.summary()["pending_sources"] == 1
    assert second.scan_cursor() == 2 and second.summary()["pending_sources"] == 2
    inventory = scan_wake_root(root)
    assert inventory.keys == {first.scope.key, second.scope.key}
    assert inventory.logical_bytes == sum(p.stat().st_size for p in root.iterdir())


def test_byte_headroom_refuses_new_generation_while_existing_work_remains_readable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    configure(root, slots=4, max_bytes=32 * 1024 * 1024)
    first = ledger(root)
    first.observe_page(0, 1, sources=("messages",))
    for n in (1, 2):
        path = ledger(root, n).path
        path.touch(mode=0o600)
        with path.open("r+b") as stream:
            stream.truncate(
                MAX_WAKE_DATABASE_BYTES - ((root / ROOT_LOCK).stat().st_size if n == 2 else 0)
            )
    denied = ledger(root, 3)
    with pytest.raises(WakeLedgerError, match="byte budget"):
        denied.summary()
    assert not denied.path.exists()
    assert first.summary()["pending_sources"] == 1


@pytest.mark.parametrize("change", ["missing", "empty"])
def test_known_active_ledger_is_never_silently_recreated(tmp_path: Path, change: str) -> None:
    current = ledger(tmp_path / "state")
    current.observe_page(0, 1, sources=("messages",))
    if change == "missing":
        current.path.unlink()
    else:
        current.path.write_bytes(b"")
    with pytest.raises(WakeLedgerError, match="disappeared"):
        current.summary()
    assert not current.path.exists() if change == "missing" else current.path.stat().st_size == 0


def test_another_process_cannot_enter_a_locked_root_or_consume_a_cursor(tmp_path: Path) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 1, sources=("messages",))
    code = """
import hashlib,sys
from pathlib import Path
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import WakeScope,WakeLedgerError
entry=WakeLedger(Path(sys.argv[1]),WakeScope('subject','ALPHA','ALPHA/worker',hashlib.sha256(b'0').hexdigest(),'root-hub'))
try:entry.observe_page(1,2,sources=('tasks',))
except WakeLedgerError as error:print(str(error))
else:raise SystemExit(9)
"""
    with locked_wake_root(root):
        result = child(root, code)
        assert result.returncode == 0 and "root is busy" in result.stdout
    assert current.scan_cursor() == 1
    assert current.observe_page(1, 2, sources=("tasks",))


def test_real_process_exit_rolls_back_ledger_and_releases_root_lock(tmp_path: Path) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 4, sources=("messages",))
    code = """
import hashlib,os,sys
from pathlib import Path
from synapse_channel.mcp.wake_database import wake_database
from synapse_channel.mcp.wake_pointer import WakeScope
scope=WakeScope('subject','ALPHA','ALPHA/worker',hashlib.sha256(b'0').hexdigest(),'root-hub')
with wake_database(Path(sys.argv[1])/(scope.key+'.sqlite3'),scope) as db:
 db.execute('UPDATE meta SET pending=7')
 print('before-exit',flush=True)
 os._exit(31)
"""
    result = child(root, code)
    assert result.returncode == 31 and result.stdout.splitlines() == ["before-exit"]
    assert current.summary()["pending_sources"] == 1
    assert current.scan_cursor() == 4
    assert current.observe_page(4, 5, sources=("tasks",))


def test_a_crash_after_reservation_does_not_lose_capacity_or_invent_consumed_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    configure(root, slots=1)
    code = """
import hashlib,os,sys
from pathlib import Path
from synapse_channel.mcp.wake_pointer import WakeScope
from synapse_channel.mcp.wake_root import locked_wake_root
from synapse_channel.mcp.wake_root_catalogue import root_catalogue
scope=WakeScope('subject','ALPHA','ALPHA/worker',hashlib.sha256(b'0').hexdigest(),'root-hub')
with locked_wake_root(Path(sys.argv[1])) as inventory:
 with root_catalogue(Path(sys.argv[1])) as catalogue:catalogue.reserve(inventory,scope.key)
 os._exit(32)
"""
    assert child(root, code).returncode == 32
    current = ledger(root)
    assert not current.path.exists()
    assert current.scan_cursor() == 0
    with locked_wake_root(root):
        with root_catalogue(root) as catalogue:
            entry = catalogue.get(current.scope.key)
            assert entry is not None and entry.phase == "active"
            assert len(catalogue.live_keys()) == 1


def test_lower_limits_and_stale_cas_preserve_the_original_configuration(tmp_path: Path) -> None:
    root = tmp_path / "state"
    configure(root, slots=2)
    for n in range(2):
        ledger(root, n).summary()
    with locked_wake_root(root) as inventory:
        with root_catalogue(root) as catalogue:
            previous = catalogue.revision()
            with pytest.raises(WakeLedgerError, match="generation capacity"):
                catalogue.configure(RootLimits(1), previous, inventory)
            assert catalogue.revision() == previous and catalogue.limits().max_ledgers == 2
            with pytest.raises(WakeLedgerError, match="configuration changed"):
                catalogue.configure(RootLimits(3), previous - 1, inventory)
            assert catalogue.limits().max_ledgers == 2


def test_root_counter_exhaustion_refuses_admission_without_creating_a_native_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 1, sources=("messages",))
    with closing(sqlite3.connect(root / ROOT_CATALOGUE)) as database, database:
        database.execute("UPDATE meta SET revision=?", (MAX_WAKE_INTEGER,))
    denied = ledger(root, 1)
    with pytest.raises(WakeLedgerError, match="revision exhausted"):
        denied.summary()
    assert not denied.path.exists() and current.scan_cursor() == 1


@pytest.mark.parametrize(
    "bad", ["unmanaged", "directory", "symlink", "hardlink", "journal-too-large"]
)
def test_unmanaged_root_inputs_refuse_without_discarding_native_data(
    tmp_path: Path, bad: str
) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 1, sources=("messages",))
    if bad == "unmanaged":
        (root / "notes.txt").write_text("private")
    elif bad == "directory":
        ledger(root, 1).path.mkdir()
    elif bad == "symlink":
        ledger(root, 1).path.symlink_to(current.path)
    elif bad == "hardlink":
        os.link(current.path, ledger(root, 1).path)
    else:
        path = Path(str(current.path) + "-journal")
        path.touch(mode=0o600)
        with path.open("r+b") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(WakeLedgerError):
        current.summary()
    assert current.path.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"max_ledgers": True},
        {"max_ledgers": 0},
        {"max_ledgers": 1025},
        {"max_bytes": True},
        {"max_bytes": 32 * 1024 * 1024 - 1},
        {"max_bytes": 16 * 1024**3 + 1},
    ],
)
def test_root_limits_refuse_coercion_and_unbounded_values(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="root limits"):
        RootLimits(**changes)


@pytest.mark.parametrize("damage", ["missing", "empty"])
def test_missing_or_empty_root_catalogue_never_silently_discards_generation_fences(
    tmp_path: Path, damage: str
) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 1, sources=("messages",))
    path = root / ROOT_CATALOGUE
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"")
    before = current.path.read_bytes()
    with pytest.raises(WakeLedgerError):
        current.summary()
    assert current.path.read_bytes() == before
    assert not path.exists() if damage == "missing" else path.stat().st_size == 0


def test_lost_root_catalogue_and_lock_cannot_reset_a_current_native_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    current = ledger(root)
    current.observe_page(0, 1, sources=("messages",))
    (root / ROOT_CATALOGUE).unlink()
    (root / ROOT_LOCK).unlink()
    before = current.path.read_bytes()
    with pytest.raises(WakeLedgerError, match="metadata is missing"):
        current.summary()
    assert not (root / ROOT_CATALOGUE).exists()
    assert current.path.read_bytes() == before


@pytest.mark.parametrize(
    "setting",
    [
        "max_page_count",
        "journal_mode",
        "synchronous",
        "trusted_schema",
        "temp_store",
        "cache_size",
        "cache_spill",
    ],
)
def test_real_sqlite_refusal_of_a_required_setting_cannot_claim_a_bounded_profile(
    tmp_path: Path,
    setting: str,
) -> None:
    path = tmp_path / "profile.sqlite3"
    with closing(sqlite3.connect(path)) as database, database:
        if setting == "journal_mode":
            assert database.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        if setting == "synchronous":
            database.execute("PRAGMA synchronous=OFF")

        def ignore_setting(
            action: int, first: str | None, second: str | None, db: str | None, trigger: str | None
        ) -> int:
            if action == sqlite3.SQLITE_PRAGMA and first == setting and second is not None:
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_OK

        database.set_authorizer(ignore_setting)
        with pytest.raises(WakeLedgerError, match="SQLite profile unavailable"):
            configure_wake_sqlite(database)


def test_sqlite_permission_denial_of_readback_is_not_silent_success(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "profile.sqlite3")) as database, database:

        def deny_readback(
            action: int, first: str | None, second: str | None, db: str | None, trigger: str | None
        ) -> int:
            if action == sqlite3.SQLITE_PRAGMA and first == "page_size" and second is None:
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_OK

        database.set_authorizer(deny_readback)
        with pytest.raises(WakeLedgerError, match="page size"):
            configure_wake_sqlite(database)


def test_incompatible_page_size_is_refused_before_changing_existing_records(tmp_path: Path) -> None:
    path = tmp_path / "old-profile.sqlite3"
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("PRAGMA page_size=8192")
        database.execute("CREATE TABLE proof(value TEXT)")
        database.execute("INSERT INTO proof VALUES('retained')")
        database.commit()
        with pytest.raises(WakeLedgerError, match="page size"):
            configure_wake_sqlite(database)
        assert database.execute("SELECT value FROM proof").fetchone()[0] == "retained"


def test_missing_root_inspection_never_provisions_replacement_state(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    with pytest.raises(WakeLedgerError, match="root unavailable"):
        locked_wake_root(root).__enter__()
    assert not root.exists()


def test_wrong_native_filename_refuses_before_provisioning_a_root(tmp_path: Path) -> None:
    entry = ledger(tmp_path / "state")
    wrong = entry.path.with_name("wrong.sqlite3")
    with pytest.raises(WakeLedgerError, match="filename does not match"):
        wake_database(wrong, entry.scope).__enter__()
    assert not wrong.parent.exists()


def test_orphan_journal_refuses_without_inventing_a_new_native_database(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    entry = ledger(root)
    journal = Path(str(entry.path) + "-journal")
    journal.write_bytes(b"retained incomplete transaction")
    journal.chmod(0o600)
    with pytest.raises(WakeLedgerError, match="orphan journal"):
        entry.summary()
    assert not entry.path.exists()
    assert journal.read_bytes() == b"retained incomplete transaction"


def test_oversized_lock_refuses_without_mutating_pending_work(tmp_path: Path) -> None:
    entry = ledger(tmp_path / "state")
    entry.observe_page(0, 1, sources=("messages",))
    before = entry.path.read_bytes()
    (entry.path.parent / ROOT_LOCK).write_bytes(b"invalid lock")
    with pytest.raises(WakeLedgerError, match="invalid wake root lock"):
        entry.summary()
    assert entry.path.read_bytes() == before


def test_recreating_only_the_lock_retains_existing_catalogue_and_pending_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    catalogue_before = (root / ROOT_CATALOGUE).read_bytes()
    (root / ROOT_LOCK).unlink()
    assert entry.summary()["pending_sources"] == 1
    assert (root / ROOT_CATALOGUE).read_bytes() == catalogue_before


def test_legacy_schema_three_without_root_metadata_upgrades_without_losing_pending_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 7, sources=("tasks",))
    with closing(sqlite3.connect(entry.path)) as database, database:
        database.execute("PRAGMA user_version=3")
    (root / ROOT_CATALOGUE).unlink()
    (root / ROOT_LOCK).unlink()
    assert entry.summary()["pending_sources"] == 2
    assert entry.scan_cursor() == 7
    with closing(sqlite3.connect(entry.path)) as database, database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 4


def test_catalogue_journal_is_included_in_bounded_root_inventory(tmp_path: Path) -> None:
    root = tmp_path / "state"
    configure(root)
    journal = root / (ROOT_CATALOGUE + "-journal")
    journal.write_bytes(b"retained rollback pages")
    journal.chmod(0o600)
    inventory = scan_wake_root(root)
    assert inventory.sizes[journal.name] == journal.stat().st_size
    assert inventory.logical_bytes == sum(p.stat().st_size for p in root.iterdir())
    assert journal.read_bytes() == b"retained rollback pages"


def test_flat_root_inventory_stops_at_its_hard_file_count(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    for number in range(MAX_ROOT_FILES + 1):
        (root / (f"{number:064x}" + ".sqlite3")).touch(mode=0o600)
    with pytest.raises(WakeLedgerError, match="inventory exceeds its item budget"):
        scan_wake_root(root)
    assert sum(1 for _ in root.iterdir()) == MAX_ROOT_FILES + 1


@pytest.mark.parametrize("target_kind", ["catalogue", "native"])
def test_file_growth_during_real_creation_sync_refuses_before_native_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    root = tmp_path / "state"
    if target_kind == "native":
        configure(root)
    entry = ledger(root)
    target = root / ROOT_CATALOGUE if target_kind == "catalogue" else entry.path
    original_fsync = os.fsync
    injected = False

    def grow_after_real_sync(descriptor: int) -> None:
        nonlocal injected
        original_fsync(descriptor)
        if not injected and target.exists() and target.stat().st_size == 0:
            injected = True
            with target.open("r+b") as stream:
                stream.truncate(MAX_WAKE_DATABASE_BYTES + 1)

    monkeypatch.setattr(os, "fsync", grow_after_real_sync)
    with pytest.raises(WakeLedgerError, match="exceeds its byte budget"):
        entry.observe_page(0, 1, sources=("messages",))
    assert injected and target.stat().st_size == MAX_WAKE_DATABASE_BYTES + 1
    if target_kind == "catalogue":
        assert not entry.path.exists()
    else:
        with closing(sqlite3.connect(root / ROOT_CATALOGUE)) as database, database:
            assert database.execute("SELECT phase FROM entries").fetchone()[0] == "creating"


def test_real_sqlite_page_limit_rolls_back_dirty_pages_with_bounded_rollback_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    configure(root, slots=1, max_bytes=32 * 1024 * 1024)
    entry = ledger(root)
    entry.observe_page(0, 1, sources=("messages",))
    with wake_database(entry.path, entry.scope) as database:
        database.execute("CREATE TABLE pressure(value BLOB NOT NULL)")
        database.executemany("INSERT INTO pressure VALUES(zeroblob(?))", [(256 * 1024,)] * 12)
    before = entry.path.read_bytes()
    journal = Path(str(entry.path) + "-journal")
    journal_peak = 0
    logical_peak = 0
    with pytest.raises(WakeLedgerError, match="wake ledger unavailable"):
        with wake_database(entry.path, entry.scope) as database:
            database.execute("UPDATE meta SET pending=7")
            database.execute("UPDATE pressure SET value=randomblob(length(value))")
            journal_peak = journal.stat().st_size
            logical_peak = scan_wake_root(root).logical_bytes
            with pytest.raises(sqlite3.OperationalError, match="database or disk is full") as full:
                while True:
                    database.execute("INSERT INTO pressure VALUES(zeroblob(262144))")
            raise full.value
    assert 2 * 1024 * 1024 < journal_peak <= 8 * 1024 * 1024
    assert logical_peak <= 32 * 1024 * 1024
    assert entry.path.stat().st_size <= MAX_WAKE_DATABASE_BYTES
    assert entry.path.read_bytes() == before
    assert entry.scan_cursor() == 1 and entry.summary()["pending_sources"] == 1
    with closing(sqlite3.connect(entry.path)) as database, database:
        assert database.execute("SELECT count(*) FROM pressure").fetchone()[0] == 12
        assert (
            database.execute(
                "SELECT count(*) FROM pressure WHERE value!=zeroblob(length(value))"
            ).fetchone()[0]
            == 0
        )
