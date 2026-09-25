# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal, cast

import pytest

from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_file_execution import (
    ProtectedDescriptorLocks,
    create_retained_protected_entry,
    fsync_retained_protected_object,
    overwrite_retained_protected_file,
    rename_retained_protected_file,
    unlink_retained_protected_file,
)
from test_protected_write_descriptors import _observe, root

__all__ = ["root"]


def test_locks_survive_path_context_close_and_unlock_in_reverse_order(root: int) -> None:
    manager = ProtectedDescriptorLocks(2)
    try:
        with hold_protected_path(
            root, _observe(root), owner_uid=os.getuid(), max_bytes=64
        ) as target:
            manager.acquire(target)
        with hold_protected_path(
            root, _observe(root, "parent", directory=True), owner_uid=os.getuid(), max_bytes=64
        ) as parent:
            manager.acquire(parent)
            with hold_protected_path(
                root, _observe(root), owner_uid=os.getuid(), max_bytes=64
            ) as refreshed:
                with pytest.raises(ValueError, match="reverse"):
                    manager.release(refreshed)
            manager.release(parent)
        with hold_protected_path(
            root, _observe(root), owner_uid=os.getuid(), max_bytes=64
        ) as refreshed:
            manager.release(refreshed)
            with pytest.raises(ValueError, match="unlock"):
                manager.release(refreshed)
    finally:
        manager.close()


def test_real_lock_contention_and_terminal_resource_release(root: int) -> None:
    first, second = ProtectedDescriptorLocks(1), ProtectedDescriptorLocks(1)
    try:
        with hold_protected_path(root, _observe(root), owner_uid=os.getuid(), max_bytes=64) as one:
            first.acquire(one)
        with hold_protected_path(root, _observe(root), owner_uid=os.getuid(), max_bytes=64) as two:
            with pytest.raises(BlockingIOError):
                second.acquire(two)
            first.close()
            second.acquire(two)
            with pytest.raises(ValueError, match="duplicate|budget"):
                second.acquire(two)
            with pytest.raises(ValueError, match="open manager"):
                first.acquire(two)
            with pytest.raises(ValueError, match="unlock"):
                first.release(two)
    finally:
        first.close()
        second.close()


def test_lock_rejects_absent_targets(root: int) -> None:
    manager = ProtectedDescriptorLocks(1)
    try:
        with hold_protected_path(
            root, _observe(root, "parent/absent"), owner_uid=os.getuid(), max_bytes=64
        ) as target:
            with pytest.raises(ValueError, match="existing"):
                manager.acquire(target)
    finally:
        manager.close()


@pytest.mark.parametrize("budget", [True, 0, -1, 2**53])
def test_invalid_lock_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        ProtectedDescriptorLocks(cast(int, budget))


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_create_exact_entry_and_explicit_fsync(
    root: int, tmp_path: Path, kind: Literal["file", "directory"]
) -> None:
    with hold_protected_path(
        root, _observe(root, "parent/new"), owner_uid=os.getuid(), max_bytes=64
    ) as target:
        create_retained_protected_entry(
            target, kind=kind, mode=0o700, content=b"new" if kind == "file" else b"", max_bytes=64
        )
    path = tmp_path / "parent/new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert path.is_dir() == (kind == "directory")
    if kind == "file":
        assert path.read_bytes() == b"new"
    with hold_protected_path(
        root,
        _observe(root, "parent/new", directory=kind == "directory"),
        owner_uid=os.getuid(),
        max_bytes=64,
    ) as target:
        fsync_retained_protected_object(target)


@pytest.mark.parametrize("case", ["kind", "directory_content", "mode", "existing"])
def test_creation_refuses_invalid_input(root: int, tmp_path: Path, case: str) -> None:
    expected = _observe(root, "parent/file" if case == "existing" else "parent/new")
    with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64) as target:
        with pytest.raises(ValueError):
            create_retained_protected_entry(
                target,
                kind=cast(
                    Literal["file", "directory"],
                    "other"
                    if case == "kind"
                    else "directory"
                    if case == "directory_content"
                    else "file",
                ),
                mode=0o777 if case == "mode" else 0o600,
                content=b"new",
                max_bytes=64,
            )
    assert not (tmp_path / "parent/new").exists()


def test_masked_mode_is_not_repaired_or_cleaned_up(root: int, tmp_path: Path) -> None:
    with hold_protected_path(
        root, _observe(root, "parent/new"), owner_uid=os.getuid(), max_bytes=64
    ) as target:
        previous = os.umask(0o777)
        try:
            with pytest.raises(ValueError, match="mode or owner"):
                create_retained_protected_entry(
                    target, kind="file", mode=0o600, content=b"new", max_bytes=64
                )
        finally:
            os.umask(previous)
    path = tmp_path / "parent/new"
    assert stat.S_IMODE(path.stat().st_mode) == 0
    assert path.stat().st_size == 0


@pytest.mark.parametrize("existing", [False, True])
def test_rename_preserves_source_object_then_explicit_unlink(
    root: int, tmp_path: Path, existing: bool
) -> None:
    original = (tmp_path / "parent/file").stat().st_ino
    destination = tmp_path / "parent/destination"
    if existing:
        destination.write_bytes(b"old")
    with hold_protected_path(root, _observe(root), owner_uid=os.getuid(), max_bytes=64) as source:
        with hold_protected_path(
            root, _observe(root, "parent/destination"), owner_uid=os.getuid(), max_bytes=64
        ) as target:
            rename_retained_protected_file(source, target)
    assert not (tmp_path / "parent/file").exists()
    assert destination.stat().st_ino == original
    assert destination.read_bytes() == b"content"
    with hold_protected_path(
        root, _observe(root, "parent/destination"), owner_uid=os.getuid(), max_bytes=64
    ) as target:
        unlink_retained_protected_file(target)
    assert not destination.exists()


@pytest.mark.parametrize("case", ["alias", "absent", "directory"])
def test_rename_refuses_incompatible_objects(root: int, case: str) -> None:
    expected = _observe(
        root,
        "parent/missing"
        if case == "absent"
        else "parent"
        if case == "directory"
        else "parent/file",
        directory=case == "directory",
    )
    with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64) as target:
        with pytest.raises(ValueError):
            rename_retained_protected_file(target, target)


@pytest.mark.parametrize("directory", [False, True])
def test_unlink_refuses_absent_or_directory(root: int, directory: bool) -> None:
    with hold_protected_path(
        root,
        _observe(root, "parent" if directory else "parent/missing", directory=directory),
        owner_uid=os.getuid(),
        max_bytes=64,
    ) as target:
        with pytest.raises(ValueError, match="unlink"):
            unlink_retained_protected_file(target)
        if not directory:
            with pytest.raises(ValueError, match="fsync"):
                fsync_retained_protected_object(target)


@pytest.mark.parametrize("content", [b"", b"new", b"longer contents", b"x" * 70000])
def test_real_bounded_overwrite_preserves_inode_mode_and_offset(
    root: int, tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "parent/file"
    before = path.stat()
    with hold_protected_path(
        root, _observe(root), owner_uid=os.getuid(), max_bytes=64, writable=True
    ) as target:
        assert target.object_descriptor is not None
        os.lseek(target.object_descriptor, 2, os.SEEK_SET)
        overwrite_retained_protected_file(target, content, max_bytes=70000)
        assert os.lseek(target.object_descriptor, 0, os.SEEK_CUR) == 2
    after = path.stat()
    assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode)
    assert path.read_bytes() == content


@pytest.mark.parametrize("case", ["budget", "bytes", "overflow", "absent", "directory", "stale"])
def test_invalid_or_stale_input_does_not_write(root: int, tmp_path: Path, case: str) -> None:
    expected = _observe(
        root,
        "parent" if case == "directory" else "parent/absent" if case == "absent" else "parent/file",
        directory=case == "directory",
    )
    with hold_protected_path(
        root,
        expected,
        owner_uid=os.getuid(),
        max_bytes=64,
        writable=case not in ("absent", "directory"),
    ) as target:
        if case == "stale":
            (tmp_path / "parent/file").write_bytes(b"changed")
        with pytest.raises(ValueError):
            overwrite_retained_protected_file(
                target,
                cast(bytes, bytearray(b"new")) if case == "bytes" else b"new",
                max_bytes=cast(int, True) if case == "budget" else 1 if case == "overflow" else 64,
            )
    assert (tmp_path / "parent/file").read_bytes() == (
        b"changed" if case == "stale" else b"content"
    )


@pytest.mark.parametrize("failure", ["error", "zero"])
def test_partial_write_stops_without_rollback(
    root: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    with hold_protected_path(
        root, _observe(root), owner_uid=os.getuid(), max_bytes=64, writable=True
    ) as target:
        original = os.pwrite
        calls = 0

        def partial(descriptor: int, data: bytes, offset: int) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(descriptor, data[:2], offset)
            if failure == "zero":
                return 0
            raise OSError("injected disk error")

        # Injected write failure checks partial-state retention; activation needs a real
        # RLIMIT_FSIZE or storage-exhaustion fault.
        monkeypatch.setattr(os, "pwrite", partial)
        with pytest.raises(OSError):
            overwrite_retained_protected_file(target, b"new", max_bytes=64)
        assert calls == 2
    assert (tmp_path / "parent/file").read_bytes() == b"nentent"
