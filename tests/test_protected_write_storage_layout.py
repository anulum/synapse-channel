# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected journal layout regressions
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_storage_layout import (
    ProtectedJournalLayout,
    inspect_protected_journal_layout,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private storage layout")


@pytest.fixture
def root(tmp_path: Path) -> Iterator[int]:
    path = tmp_path / "journal.db"
    path.write_bytes(b"db")
    path.chmod(0o600)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _inspect(root: int, **overrides: Any) -> ProtectedJournalLayout:
    info = os.fstat(root)
    options: dict[str, Any] = dict(
        root_identity=(info.st_dev, info.st_ino),
        owner_uid=os.getuid(),
        max_file_bytes=1024,
        max_total_bytes=2048,
    )
    options.update(overrides)
    return inspect_protected_journal_layout(
        root, options.pop("database_name", "journal.db"), **options
    )


def test_exact_layout_and_borrowed_descriptor_lifetime(root: int, tmp_path: Path) -> None:
    snapshot = _inspect(root)
    assert snapshot.files == (
        (
            "journal.db",
            (tmp_path / "journal.db").stat().st_dev,
            (tmp_path / "journal.db").stat().st_ino,
            2,
        ),
    )
    os.fstat(root)


@pytest.mark.parametrize(
    "options",
    [
        {"owner_uid": True},
        {"root_identity": (True, 1)},
        {"root_identity": ()},
        {"max_file_bytes": 0},
        {"max_total_bytes": True},
        {"database_name": "../journal.db"},
        {"root_identity": (0, 0)},
        {"owner_uid": -1},
    ],
)
def test_invalid_enrollment(root: int, options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _inspect(root, **options)


@pytest.mark.parametrize(
    "case",
    [
        "root_mode",
        "file_mode",
        "symlink",
        "hardlink",
        "directory",
        "rollback",
        "file_budget",
        "total_budget",
        "missing",
    ],
)
def test_unsafe_layout_is_rejected_without_repair(root: int, tmp_path: Path, case: str) -> None:
    path = tmp_path / "journal.db"
    options: dict[str, Any] = {}
    if case == "root_mode":
        tmp_path.chmod(0o755)
    elif case == "file_mode":
        path.chmod(0o644)
    elif case == "symlink":
        path.rename(tmp_path / "actual")
        path.symlink_to("actual")
    elif case == "hardlink":
        os.link(path, tmp_path / "alias")
    elif case == "directory":
        path.unlink()
        path.mkdir(mode=0o600)
    elif case == "rollback":
        (tmp_path / "journal.db-journal").write_bytes(b"leftover")
    elif case == "file_budget":
        options["max_file_bytes"] = 1
    elif case == "total_budget":
        options["max_total_bytes"] = 1
    else:
        path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        _inspect(root, **options)
    os.fstat(root)
    if case == "file_mode":
        assert path.stat().st_mode & 0o777 == 0o644


def test_replacement_during_open_is_detected(
    root: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.open

    def changed(path: str, flags: int, *, dir_fd: int | None = None) -> int:
        target = tmp_path / path
        target.rename(tmp_path / "old")
        target.write_bytes(b"db")
        target.chmod(0o600)
        return original(path, flags, dir_fd=dir_fd)

    # Injected descriptor race checks refusal; activation needs a competing process changing the
    # target.
    monkeypatch.setattr(os, "open", changed)
    with pytest.raises(ValueError, match="changed during"):
        _inspect(root)
    os.fstat(root)


def test_real_sqlite_sidecars_and_clean_close(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "journal.db")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        snapshot = _inspect(root, max_file_bytes=8 * 1024**2, max_total_bytes=16 * 1024**2)
        assert {entry[0] for entry in snapshot.files} == {
            "journal.db",
            "journal.db-wal",
            "journal.db-shm",
        }
        journal.close()
        after = _inspect(root, max_file_bytes=8 * 1024**2, max_total_bytes=16 * 1024**2)
        assert [entry[0] for entry in after.files] == ["journal.db"]
    finally:
        journal.close()
        os.close(root)
