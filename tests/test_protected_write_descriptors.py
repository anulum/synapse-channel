# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained protected path descriptors
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.protected_write_descriptors import hold_protected_path
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    inspect_protected_file,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="retained POSIX descriptors")


def _observe(
    root: int, path: str = "parent/file", *, directory: bool = False
) -> ProtectedFileInspection:
    info = os.fstat(root)
    return inspect_protected_file(
        root,
        path,
        root_identity=(info.st_dev, info.st_ino),
        max_bytes=64,
        allow_directory=directory,
    )


@pytest.fixture
def root(tmp_path: Path) -> Iterator[int]:
    (tmp_path / "parent").mkdir(mode=0o700)
    (tmp_path / "parent/file").write_bytes(b"content")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("writable", [False, True])
def test_retains_exact_file_without_truncating_and_preserves_offset(
    root: int, writable: bool
) -> None:
    expected = _observe(root)
    with hold_protected_path(
        root, expected, owner_uid=os.getuid(), max_bytes=64, writable=writable
    ) as held:
        assert held.object_descriptor is not None
        descriptor = held.object_descriptor
        os.lseek(descriptor, 2, os.SEEK_SET)
        held.revalidate()
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 2
        assert os.pread(descriptor, 64, 0) == b"content"
    os.fstat(root)
    with pytest.raises(OSError):
        os.fstat(descriptor)
    with pytest.raises(ValueError, match="closed"):
        held.revalidate()


@pytest.mark.parametrize("directory", [False, True])
def test_retains_absent_leaf_or_directory(root: int, directory: bool) -> None:
    expected = _observe(root, "parent" if directory else "parent/absent", directory=directory)
    with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64) as held:
        held.revalidate()
        assert (held.object_descriptor is not None) == directory


@pytest.mark.parametrize(
    "change",
    [
        "rename_parent",
        "replace_file",
        "unlink",
        "mode",
        "bytes",
        "size",
        "hardlink",
        "appeared",
        "owner_control",
    ],
)
def test_detects_real_changes_while_retained(root: int, tmp_path: Path, change: str) -> None:
    path = tmp_path / "parent/file"
    if change == "appeared":
        path.unlink()
    expected = _observe(root)
    with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64) as held:
        if change == "rename_parent":
            (tmp_path / "parent").rename(tmp_path / "old")
            (tmp_path / "parent").mkdir(mode=0o700)
        elif change == "replace_file":
            path.rename(path.with_name("old"))
            path.write_bytes(b"content")
        elif change == "unlink":
            path.unlink()
        elif change == "mode":
            path.chmod(0o600 if expected.mode != 0o600 else 0o400)
        elif change == "bytes":
            path.write_bytes(b"CONTENT")
        elif change == "size":
            path.write_bytes(b"larger content")
        elif change == "hardlink":
            os.link(path, path.with_name("alias"))
        elif change == "appeared":
            path.write_bytes(b"new")
        else:
            (tmp_path / "parent").chmod(0o777)
        with pytest.raises(ValueError):
            held.revalidate()


def test_early_observation_change_does_not_open_writable_leaf(root: int, tmp_path: Path) -> None:
    expected = _observe(root)
    (tmp_path / "parent/file").write_bytes(b"changed")
    with pytest.raises(ValueError, match="before retention"):
        with hold_protected_path(
            root, expected, owner_uid=os.getuid(), max_bytes=64, writable=True
        ):
            pytest.fail("changed observation accepted")
    assert (tmp_path / "parent/file").read_bytes() == b"changed"


@pytest.mark.parametrize(
    "field",
    [
        "uid_bool",
        "uid_negative",
        "option",
        "root",
        "absent_writable",
        "directory_writable",
        "foreign_owner",
    ],
)
def test_invalid_enrollment_and_access(root: int, field: str) -> None:
    expected = _observe(root)
    uid = os.getuid()
    writable = False
    if field == "uid_bool":
        uid = cast(int, True)
    elif field == "uid_negative":
        uid = -1
    elif field == "option":
        writable = cast(bool, 1)
    elif field == "root":
        expected = replace(expected, directories=())
    elif field == "absent_writable":
        expected = _observe(root, "parent/absent")
        writable = True
    elif field == "directory_writable":
        expected = _observe(root, "parent", directory=True)
        writable = True
    else:
        uid += 1
    with pytest.raises(ValueError):
        with hold_protected_path(root, expected, owner_uid=uid, max_bytes=64, writable=writable):
            pytest.fail("invalid enrollment accepted")


def test_context_exception_closes_owned_descriptors_only(root: int) -> None:
    with pytest.raises(RuntimeError, match="caller failed"):
        with hold_protected_path(root, _observe(root), owner_uid=os.getuid(), max_bytes=64) as held:
            parent = held.parent_descriptor
            raise RuntimeError("caller failed")
    with pytest.raises(OSError):
        os.fstat(parent)
    os.fstat(root)


@pytest.mark.parametrize("remove", [False, True])
def test_directory_changes_between_inspection_and_retention(
    root: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remove: bool,
) -> None:
    expected = _observe(root)
    original_open = os.open

    def changing_open(
        path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        # Trigger a real rename immediately before retaining the already inspected
        # parent. Return only actual OS descriptors, never fabricated stat results.
        if path == "parent":
            (tmp_path / "parent").rename(tmp_path / "old")
            if not remove:
                (tmp_path / "parent").mkdir(mode=0o700)
                (tmp_path / "parent/file").write_bytes(b"content")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    # Install only after the ordinary initial read inside hold_protected_path.
    from synapse_channel.core import protected_write_descriptors as owner

    original_inspect = inspect_protected_file

    def inspected(*args: object, **kwargs: object) -> ProtectedFileInspection:
        observation = original_inspect(
            root, expected.relative_path, root_identity=expected.directories[0], max_bytes=64
        )
        # Injected open race checks refusal; activation needs a competing process replacing the
        # path.
        monkeypatch.setattr(os, "open", changing_open)
        return observation

    # Injected inspection race checks refusal; activation needs a real concurrent rename or
    # replacement.
    monkeypatch.setattr(owner, "inspect_protected_file", inspected)
    with pytest.raises((ValueError, FileNotFoundError)):
        with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64):
            pytest.fail("replacement topology accepted")
    os.fstat(root)


def test_growth_during_real_positional_read_is_bounded(
    root: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _observe(root)
    with hold_protected_path(root, expected, owner_uid=os.getuid(), max_bytes=64) as held:
        original_pread = os.pread
        triggered = False

        def growing_read(descriptor: int, count: int, offset: int) -> bytes:
            nonlocal triggered
            if not triggered:
                triggered = True
                (tmp_path / "parent/file").write_bytes(b"x" * 65)
            return original_pread(descriptor, count, offset)

        # Injected growth checks refusal; activation needs real concurrent file growth through a
        # second process.
        monkeypatch.setattr(os, "pread", growing_read)
        with pytest.raises(ValueError, match="grew beyond"):
            held.revalidate()
    assert (tmp_path / "parent/file").stat().st_size == 65
