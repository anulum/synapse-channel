# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Windows opens a leaf without following a reparse point
"""The Windows opener is one handle, then a descriptor."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from _platform_caps import create_link_or_skip
from synapse_channel.core.secure_path import (
    _windows_kernel,
    _windows_open_nofollow,
    open_nofollow_leaf,
)


def test_a_non_windows_host_has_no_reparse_opener() -> None:
    """The Win32 opener refuses to run where kernel32 is not the host kernel."""
    if sys.platform == "win32":
        kernel = _windows_kernel()
        again = _windows_kernel()
        assert again is kernel
        return
    with pytest.raises(OSError, match="the Win32 kernel is unavailable"):
        _windows_kernel()
    with pytest.raises(OSError, match="secure nofollow open is unavailable"):
        _windows_open_nofollow(Path("leaf"), directory=False)


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_a_regular_file_is_read_from_the_opened_handle(tmp_path: Path) -> None:
    """The descriptor returns the leaf's own bytes."""
    path = tmp_path / "leaf"
    path.write_bytes(b"data\n")
    descriptor = open_nofollow_leaf(path)
    try:
        assert os.read(descriptor, 16) == b"data\n"
        assert stat.S_ISREG(os.fstat(descriptor).st_mode)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_a_missing_leaf_is_not_opened(tmp_path: Path) -> None:
    """A path that is not there raises before a descriptor exists."""
    with pytest.raises(FileNotFoundError):
        open_nofollow_leaf(tmp_path / "absent")


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_a_directory_handle_is_rejected(tmp_path: Path) -> None:
    """A directory is not given backup semantics, so the kernel rejects it."""
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        open_nofollow_leaf(directory, directory=True)


def test_a_refused_symlink_creation_is_reported_as_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process that cannot create a link reports that, and this test passes."""

    def refuse(self: Path, target: str | Path, target_is_directory: bool = False) -> None:
        del self, target, target_is_directory
        raise OSError("privilege")

    monkeypatch.setattr(Path, "symlink_to", refuse)
    with pytest.raises(pytest.skip.Exception, match="cannot create a symlink"):
        create_link_or_skip(tmp_path / "link", tmp_path / "leaf")


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_a_reparse_point_is_refused(tmp_path: Path) -> None:
    """The handle is the link itself, and that reparse point is not followed."""
    path = tmp_path / "leaf"
    path.write_bytes(b"data\n")
    link = tmp_path / "link"
    create_link_or_skip(link, path)
    with pytest.raises(OSError, match="symlink refused"):
        open_nofollow_leaf(link)


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_an_attribute_read_failure_does_not_return_a_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed attribute read closes the handle and raises."""
    path = tmp_path / "leaf"
    path.write_bytes(b"data\n")
    kernel = _windows_kernel()

    def fail(handle: int, info: object) -> bool:
        del handle, info
        return False

    monkeypatch.setattr(kernel, "GetFileInformationByHandle", fail)
    with pytest.raises(OSError):
        open_nofollow_leaf(path)


@pytest.mark.skipif(os.name != "nt", reason="CreateFileW reparse refusal is measured on Windows")
def test_a_descriptor_failure_closes_the_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed conversion to a descriptor closes the kernel handle."""
    import msvcrt

    path = tmp_path / "leaf"
    path.write_bytes(b"data\n")

    def fail(handle: int, flags: int) -> int:
        del handle, flags
        raise OSError("descriptor refused")

    monkeypatch.setattr(msvcrt, "open_osfhandle", fail)
    with pytest.raises(OSError, match="descriptor refused"):
        open_nofollow_leaf(path)
