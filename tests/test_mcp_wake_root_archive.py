# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake archive recovery contracts
"""Preserve real raw generation state through owner archival and interruption recovery."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_limits import ROOT_CATALOGUE
from synapse_channel.mcp.wake_pointer import WakeLedgerError
from synapse_channel.mcp.wake_root_archive import ArchiveSnapshot, verify_wake_archive
from synapse_channel.mcp.wake_storage_control import WakeStorageControl
from test_mcp_wake_control import operator_ledger


def inactive_generation(
    directory: Path,
) -> tuple[Path, WakeLedger, WakeStorageControl, dict[str, Any]]:
    """Rotate a real synthetic grant while retaining its original failed delivery."""
    path, ledger, _ = operator_ledger(directory)
    policy = json.loads(path.read_text())
    policy["subjects"]["alice"]["projects"]["ALPHA"]["wake"]["url"] += "/replacement"
    path.write_text(json.dumps(policy))
    control = WakeStorageControl(path)
    info: dict[str, Any] = control.inspect(ledger.scope.key)
    return path, ledger, control, info


def archive(control: WakeStorageControl, info: dict[str, Any], parent: Path) -> dict[str, Any]:
    """Use the complete operator snapshot and policy fences from the public inspection."""
    return control.archive(info["key"], info["fingerprint"], info["policy_digest"], parent, 100)


def archive_access_snapshot(path: Path) -> str:
    """Read the actual POSIX mode or Windows access list without changing it."""
    if os.name == "nt":
        return subprocess.run(
            ["icacls", str(path)], check=True, capture_output=True, text=True, timeout=10
        ).stdout
    return oct(stat.S_IMODE(path.stat().st_mode))


def test_inactive_generation_archival_preserves_failure_pending_and_raw_evidence(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    path, old, control, info = inactive_generation(tmp_path)
    old.observe_page(1, 2, sources=("tasks",))
    info = control.inspect(old.scope.key)
    assert info["transport"] == {
        "pending_sources": 2,
        "active_deliveries": 0,
        "unresolved_failures": 1,
    }
    raw = old.path.read_bytes()
    result = archive(control, info, tmp_path / "archive")
    directory = Path(result["archive_directory"])
    assert result["outcome"] == "archived" and not old.path.exists()
    assert (directory / old.path.name).read_bytes() == raw
    snapshot = ArchiveSnapshot.from_json((directory / "manifest.json").read_text())
    assert snapshot.fingerprint == info["fingerprint"]
    verify_wake_archive(directory, snapshot)
    restored = tmp_path_factory.mktemp("restore") / "state.sqlite3"
    restored.write_bytes((directory / old.path.name).read_bytes())
    restored.chmod(0o600)
    with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as db, db:
        assert db.execute("SELECT pending FROM meta").fetchone()[0] == 2
        assert db.execute("SELECT state FROM deliveries").fetchone()[0] == "dead-letter"
    assert archive(control, info, tmp_path / "archive")["outcome"] == "already_archived"
    assert control.inspect(old.scope.key)["archive_directory"] == str(directory)
    assert control.status()["occupied_generations"] == 0
    with pytest.raises(WakeLedgerError, match="archived"):
        old.summary()
    assert not old.path.exists()
    admission = WakeAdmission(path, "alice", "ALPHA", load_http_auth_config(path))
    current = WakeLedger(old.path.parent, admission.scope("operator-hub"))
    assert current.observe_page(0, 1, sources=("messages",))
    assert current.summary()["pending_sources"] == 1


def test_current_enabled_generation_cannot_be_archived(tmp_path: Path) -> None:
    path, current, _ = operator_ledger(tmp_path)
    control = WakeStorageControl(path)
    info: dict[str, Any] = control.inspect(current.scope.key)
    original = current.path.read_bytes()
    with pytest.raises(WakeLedgerError, match="current wake generation"):
        archive(control, info, tmp_path / "archive")
    assert current.path.read_bytes() == original and not (tmp_path / "archive").exists()


@pytest.mark.parametrize("case", ["policy", "snapshot", "inside-root", "timestamp", "loose-parent"])
def test_stale_or_unsafe_archive_request_has_no_source_removal(tmp_path: Path, case: str) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    parent = tmp_path / "archive"
    now: Any = 100
    original_access = None
    if case == "policy":
        info["policy_digest"] = "0" * 64
    elif case == "snapshot":
        info["fingerprint"] = "0" * 64
    elif case == "inside-root":
        parent = old.path.parent / "archive"
    elif case == "timestamp":
        now = True
    else:
        parent.mkdir(mode=0o700)
        if os.name == "nt":
            subprocess.run(
                ["icacls", str(parent), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        else:
            parent.chmod(0o755)
        original_access = archive_access_snapshot(parent)
    original = old.path.read_bytes()
    with pytest.raises((WakeLedgerError, ValueError)):
        control.archive(info["key"], info["fingerprint"], info["policy_digest"], parent, now)
    assert old.path.read_bytes() == original
    assert control.inspect(old.scope.key)["phase"] == "active"
    if case == "loose-parent":
        assert archive_access_snapshot(parent) == original_access


def test_late_native_state_change_refuses_the_observed_snapshot(tmp_path: Path) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    old.observe_page(1, 2, sources=("messages",))
    with pytest.raises(WakeLedgerError, match="snapshot changed"):
        archive(control, info, tmp_path / "archive")
    assert old.scan_cursor() == 2 and old.summary()["pending_sources"] == 1


def test_copy_failure_can_retry_without_resetting_or_losing_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    original = old.path.read_bytes()
    real_sync = os.fsync

    def disk_full(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.ENOSPC, "synthetic archive disk full")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(WakeLedgerError):
        archive(control, info, tmp_path / "archive")
    monkeypatch.setattr(os, "fsync", real_sync)
    assert old.path.read_bytes() == original
    assert control.inspect(old.scope.key)["phase"] == "archiving"
    with pytest.raises(WakeLedgerError, match="retiring"):
        old.summary()
    assert archive(control, info, tmp_path / "archive")["outcome"] == "archived"


def test_cancel_preserves_original_and_existing_destination_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    original = old.path.read_bytes()
    real_link = os.link

    def refuse_link(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "synthetic publication failure")

    monkeypatch.setattr(os, "link", refuse_link)
    with pytest.raises(WakeLedgerError):
        archive(control, info, tmp_path / "archive")
    monkeypatch.setattr(os, "link", real_link)
    partials = list((tmp_path / "archive").rglob("*.partial"))
    assert len(partials) == 1
    assert (
        control.cancel(info["key"], info["fingerprint"], info["policy_digest"])["outcome"]
        == "cancelled"
    )
    assert all(path.exists() for path in partials)
    assert old.path.read_bytes() == original and old.summary()["unresolved_failures"] == 1


def crash_archive(
    path: Path, parent: Path, info: dict[str, Any], boundary: str
) -> subprocess.CompletedProcess[str]:
    """Kill an independent process immediately after a real publication/removal operation."""
    code = """
import json,os,sys
from pathlib import Path
from synapse_channel.mcp.wake_storage_control import WakeStorageControl
info=json.loads(sys.argv[3])
boundary=sys.argv[4]
if boundary=='link':
 original=os.link
 def stop_after_link(*args,**kwargs):
  original(*args,**kwargs)
  os._exit(33)
 os.link=stop_after_link
else:
 original=Path.unlink
 def stop_after_unlink(self,*args,**kwargs):
  original(self,*args,**kwargs)
  if self.name==info['key']+'.sqlite3':os._exit(34)
 Path.unlink=stop_after_unlink
WakeStorageControl(Path(sys.argv[1])).archive(info['key'],info['fingerprint'],info['policy_digest'],Path(sys.argv[2]),100)
"""
    return subprocess.run(
        [sys.executable, "-c", code, str(path), str(parent), json.dumps(info), boundary],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )


@pytest.mark.parametrize("boundary", ["link", "unlink"])
def test_real_process_exit_resumes_exact_archive_intent(tmp_path: Path, boundary: str) -> None:
    path, old, control, info = inactive_generation(tmp_path)
    original = old.path.read_bytes()
    result = crash_archive(path, tmp_path / "archive", info, boundary)
    assert result.returncode == (33 if boundary == "link" else 34), result.stderr
    assert control.inspect(old.scope.key)["phase"] == "archiving"
    if boundary == "link":
        assert old.path.read_bytes() == original
    else:
        assert not old.path.exists()
        with pytest.raises(WakeLedgerError):
            control.cancel(info["key"], info["fingerprint"], info["policy_digest"])
    completed = archive(control, info, tmp_path / "archive")
    assert completed["outcome"] == "archived"
    assert (Path(completed["archive_directory"]) / old.path.name).read_bytes() == original


@pytest.mark.parametrize("change", ["destination", "fingerprint", "reappeared"])
def test_conflicting_repeat_never_overwrites_archive_or_discards_new_source(
    tmp_path: Path, change: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    original = old.path.read_bytes()
    completed = archive(control, info, tmp_path / "archive")
    parent = tmp_path / "archive"
    if change == "destination":
        parent = tmp_path / "other"
    elif change == "fingerprint":
        info["fingerprint"] = "0" * 64
    else:
        old.path.touch(mode=0o600)
        old.path.write_bytes(original)
    with pytest.raises(WakeLedgerError):
        archive(control, info, parent)
    assert (Path(completed["archive_directory"]) / old.path.name).read_bytes() == original
    if change == "reappeared":
        assert old.path.read_bytes() == original


def interrupt_archive(
    control: WakeStorageControl,
    info: dict[str, Any],
    parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Leave a genuine committed intent and private partial after publication fails."""

    def unavailable(source: Path, destination: Path) -> None:
        raise OSError(errno.ENOSPC, "synthetic archive publication unavailable")

    with monkeypatch.context() as context:
        context.setattr(os, "link", unavailable)
        with pytest.raises(WakeLedgerError):
            archive(control, info, parent)
    partial = list(parent.rglob("*.partial"))
    assert len(partial) == 1
    return partial[0]


@pytest.mark.parametrize(
    "damage",
    ["json", "shape", "version", "file-shape", "file-name", "bytes", "digest", "key", "binding"],
)
def test_corrupted_stored_manifest_refuses_owner_inspection_without_touching_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    interrupt_archive(control, info, tmp_path / "archive", monkeypatch)
    with closing(sqlite3.connect(old.path.parent / ROOT_CATALOGUE)) as database, database:
        encoded = database.execute(
            "SELECT manifest FROM entries WHERE scope_key=?", (old.scope.key,)
        ).fetchone()[0]
        value = json.loads(encoded)
        if damage == "json":
            raw = "{"
        elif damage == "shape":
            raw = "[]"
        else:
            if damage == "version":
                value["version"] = True
            elif damage == "file-shape":
                value["files"][0] = {"unexpected": 1}
            elif damage == "file-name":
                value["files"][0]["name"] = "../outside"
            elif damage == "bytes":
                value["files"][0]["bytes"] = True
            elif damage == "digest":
                value["files"][0]["sha256"] = "invalid"
            elif damage == "key":
                value["scope_key"] = "invalid"
            else:
                value["files"][0]["name"] = "f" * 64 + ".sqlite3"
            raw = json.dumps(value)
        database.execute("UPDATE entries SET manifest=? WHERE scope_key=?", (raw, old.scope.key))
    with pytest.raises(WakeLedgerError):
        control.inspect(old.scope.key)
    assert old.path.read_bytes() == before


def test_archive_manifest_decoder_has_a_finite_input_budget() -> None:
    with pytest.raises(WakeLedgerError, match="invalid wake archive manifest"):
        ArchiveSnapshot.from_json(" " * 8193)


@pytest.mark.parametrize("action", ["inspect", "key", "fingerprint", "path-budget"])
def test_invalid_archive_selectors_refuse_before_mutating_sources(
    tmp_path: Path, action: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    parent = tmp_path / "archive"
    if action == "key":
        info["key"] = "../outside"
    elif action == "fingerprint":
        info["fingerprint"] = "invalid"
    elif action == "path-budget":
        parent = tmp_path.joinpath(*(["x" * 100] * 45))
    with pytest.raises(WakeLedgerError):
        if action == "inspect":
            control.inspect("../outside")
        else:
            archive(control, info, parent)
    assert old.path.read_bytes() == before


def test_archive_preserves_the_complete_raw_main_and_rollback_journal(tmp_path: Path) -> None:
    _, old, control, _ = inactive_generation(tmp_path)
    journal = Path(str(old.path) + "-journal")
    journal.write_bytes(b"retained incomplete rollback")
    journal.chmod(0o600)
    before = {p.name: p.read_bytes() for p in (old.path, journal)}
    info = control.inspect(old.scope.key)
    result = archive(control, info, tmp_path / "archive")
    directory = Path(result["archive_directory"])
    assert len(ArchiveSnapshot.from_json((directory / "manifest.json").read_text()).files) == 2
    for name, raw in before.items():
        assert (directory / name).read_bytes() == raw
        assert not (old.path.parent / name).exists()


@pytest.mark.parametrize("damage", ["extra-file", "oversized-partial", "final-bytes"])
def test_conflicting_archive_files_are_preserved_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    parent = tmp_path / "archive"
    if damage == "final-bytes":
        result = archive(control, info, parent)
        target = Path(result["archive_directory"]) / old.path.name
        changed = bytes([before[0] ^ 1]) + before[1:]
        target.write_bytes(changed)
    else:
        target = interrupt_archive(control, info, parent, monkeypatch)
        if damage == "oversized-partial":
            with target.open("ab") as stream:
                stream.write(b"x")
        else:
            target = target.parent / "unexpected.txt"
            target.write_bytes(b"retained unrelated file")
            target.chmod(0o600)
        changed = target.read_bytes()
    with pytest.raises(WakeLedgerError):
        archive(control, info, parent)
    assert target.read_bytes() == changed
    if damage != "final-bytes":
        assert old.path.read_bytes() == before


def test_actual_final_name_creation_race_does_not_overwrite_a_matching_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    real_link = os.link
    raced = False

    def competing_final(source: Path, destination: Path) -> None:
        nonlocal raced
        if not raced:
            raced = True
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o600)
        real_link(source, destination)

    monkeypatch.setattr(os, "link", competing_final)
    result = archive(control, info, tmp_path / "archive")
    assert raced and not old.path.exists()
    assert (Path(result["archive_directory"]) / old.path.name).read_bytes() == before


@pytest.mark.parametrize("damage", ["separate-inodes", "changed-linked-bytes"])
def test_interrupted_publication_repairs_only_the_exact_matching_two_link_inode(
    tmp_path: Path, damage: str
) -> None:
    path, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    parent = tmp_path / "archive"
    assert crash_archive(path, parent, info, "link").returncode == 33
    partial = next(parent.rglob("*.partial"))
    final = partial.parent / old.path.name
    if damage == "separate-inodes":
        raw = partial.read_bytes()
        partial.unlink()
        partial.write_bytes(raw)
        partial.chmod(0o600)
        assert not partial.samefile(final)
        result = archive(control, info, parent)
        assert result["outcome"] == "archived" and partial.read_bytes() == before
    else:
        changed = bytes([before[0] ^ 1]) + before[1:]
        final.write_bytes(changed)
        with pytest.raises(WakeLedgerError, match="publication changed"):
            archive(control, info, parent)
        assert final.samefile(partial) and final.read_bytes() == changed
        assert old.path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["source-bytes", "new-journal"])
def test_source_changes_during_real_archive_sync_never_remove_original_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    parent = tmp_path / "archive"
    directory = parent / (info["key"] + "-" + info["fingerprint"])
    real_sync = os.fsync
    changed = False

    def alter_after_real_sync(descriptor: int) -> None:
        nonlocal changed
        real_sync(descriptor)
        watched = directory if mutation == "source-bytes" else parent
        if changed or not watched.exists() or os.fstat(descriptor).st_ino != watched.stat().st_ino:
            return
        if mutation == "source-bytes":
            changed = True
            old.path.write_bytes(bytes([before[0] ^ 1]) + before[1:])
        elif (directory / "manifest.json").exists():
            changed = True
            journal = Path(str(old.path) + "-journal")
            journal.write_bytes(b"late rollback work")
            journal.chmod(0o600)

    monkeypatch.setattr(os, "fsync", alter_after_real_sync)
    refusal = "bytes changed" if mutation == "source-bytes" else "source changed"
    with pytest.raises(WakeLedgerError, match=refusal):
        archive(control, info, parent)
    assert changed and old.path.exists()
    if mutation == "new-journal":
        assert old.path.read_bytes() == before
        assert Path(str(old.path) + "-journal").read_bytes() == b"late rollback work"
    else:
        assert old.path.read_bytes() == bytes([before[0] ^ 1]) + before[1:]


def test_source_change_at_real_copy_open_refuses_before_publishing_archive_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    before = old.path.read_bytes()
    parent = tmp_path / "archive"
    directory = parent / (info["key"] + "-" + info["fingerprint"])
    original_open = os.open
    changed = False

    def alter_before_real_open(path: str | Path, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal changed
        if not changed and Path(path) == old.path and directory.exists():
            changed = True
            old.path.write_bytes(bytes([before[0] ^ 1]) + before[1:])
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", alter_before_real_open)
    with pytest.raises(WakeLedgerError, match="source changed"):
        archive(control, info, parent)
    assert changed and old.path.read_bytes() == bytes([before[0] ^ 1]) + before[1:]
    assert not (directory / "manifest.json").exists()
