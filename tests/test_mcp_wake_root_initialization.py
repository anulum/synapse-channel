# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake root initialization lifecycle contracts
"""Preserve initialization and retirement history across first-operation failures."""

from __future__ import annotations

import errno
import os
import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.secure_path import apply_owner_only_file
from synapse_channel.durable_dir import private_dir
from synapse_channel.mcp.wake_limits import ROOT_CATALOGUE, ROOT_INITIALIZED_LOCK_BYTES, ROOT_LOCK
from synapse_channel.mcp.wake_pointer import WakeLedgerError
from synapse_channel.mcp.wake_root import locked_wake_root
from synapse_channel.mcp.wake_root_catalogue import root_catalogue
from test_mcp_wake_root import child, ledger
from test_mcp_wake_root_archive import archive, inactive_generation


def test_first_inventory_refusal_can_retry_without_resetting_a_catalogue(tmp_path: Path) -> None:
    root = tmp_path / "state"
    private_dir(root, purpose="test root")
    unexpected = root / "unexpected.txt"
    unexpected.write_bytes(b"operator input")
    entry = ledger(root)
    with pytest.raises(WakeLedgerError, match="unexpected file"):
        entry.summary()
    assert not (root / ROOT_CATALOGUE).exists() and not entry.path.exists()
    unexpected.unlink()
    assert entry.summary()["pending_sources"] == 0
    assert (root / ROOT_LOCK).stat().st_size == ROOT_INITIALIZED_LOCK_BYTES


def test_marker_sync_failure_preserves_initialized_metadata_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    lock = root / ROOT_LOCK
    real_sync = os.fsync
    failed = False

    def unavailable(descriptor: int) -> None:
        nonlocal failed
        info = os.fstat(descriptor)
        if lock.exists() and (info.st_dev, info.st_ino) == (lock.stat().st_dev, lock.stat().st_ino):
            failed = True
            raise OSError(errno.ENOSPC, "synthetic marker sync failure")
        real_sync(descriptor)

    with monkeypatch.context() as context:
        context.setattr(os, "fsync", unavailable)
        with pytest.raises(WakeLedgerError, match="wake ledger unavailable"):
            entry.summary()
    assert failed and not entry.path.exists()
    with sqlite3.connect(root / ROOT_CATALOGUE) as database:
        assert database.execute("SELECT revision FROM meta").fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
    assert entry.summary()["pending_sources"] == 0


def test_actual_process_exit_after_metadata_commit_recovers_before_native_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = """
import hashlib,os,sys
from pathlib import Path
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import WakeScope
root=Path(sys.argv[1]);lock=root/'.root.lock';original=os.fsync
def exit_at_marker(fd):
 info=os.fstat(fd)
 if lock.exists() and (info.st_dev,info.st_ino)==(lock.stat().st_dev,lock.stat().st_ino):
  print('at-marker',flush=True);os._exit(41)
 original(fd)
os.fsync=exit_at_marker
WakeLedger(root,WakeScope('subject','ALPHA','ALPHA/worker',hashlib.sha256(b'0').hexdigest(),'root-hub')).summary()
"""
    result = child(root, code)
    assert result.returncode == 41 and result.stdout.splitlines() == ["at-marker"]
    entry = ledger(root)
    assert not entry.path.exists()
    assert entry.scan_cursor() == 0 and entry.summary()["pending_sources"] == 0


def test_explicit_creation_request_never_reinitializes_existing_catalogue(tmp_path: Path) -> None:
    root = tmp_path / "state"
    entry = ledger(root)
    entry.observe_page(0, 7, sources=("messages",))
    before = (root / ROOT_CATALOGUE).read_bytes()
    with locked_wake_root(root):
        with root_catalogue(root, allow_create=True) as catalogue:
            current = catalogue.get(entry.scope.key)
            assert current is not None and current.phase == "active"
    assert (root / ROOT_CATALOGUE).read_bytes() == before
    assert entry.scan_cursor() == 7


def test_retired_only_root_cannot_forget_its_missing_catalogue(tmp_path: Path) -> None:
    _, old, control, info = inactive_generation(tmp_path)
    result = archive(control, info, tmp_path / "archive")
    directory = Path(result["archive_directory"])
    before = (directory / old.path.name).read_bytes()
    assert control.status()["phases"] == {"retired": 1}
    catalogue = old.path.parent / ROOT_CATALOGUE
    catalogue.unlink()
    with pytest.raises(WakeLedgerError, match="catalogue metadata is missing"):
        control.status()
    assert not catalogue.exists() and not old.path.exists()
    assert (directory / old.path.name).read_bytes() == before


def test_incomplete_catalogue_bytes_are_preserved_even_before_initialization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    private_dir(root, purpose="test root")
    catalogue = root / ROOT_CATALOGUE
    catalogue.touch()
    apply_owner_only_file(catalogue)
    entry = ledger(root)
    with pytest.raises(WakeLedgerError, match="catalogue is incomplete"):
        entry.summary()
    assert catalogue.stat().st_size == 0 and not entry.path.exists()
