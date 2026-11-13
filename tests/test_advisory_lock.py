# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one advisory lock, held and refused, on this host
"""The public advisory lock holds one file and refuses a second taker."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

import synapse_channel.advisory_lock as advisory_lock
from synapse_channel.advisory_lock import locked


def test_a_second_taker_does_not_hold_the_lock(tmp_path: Path) -> None:
    """One holder excludes another, and the file can be held again after release."""
    path = tmp_path / "nest" / "lock"

    with locked(path, blocking=False, purpose="advisory lock test") as held:
        assert held is True
        with locked(path, blocking=False, purpose="advisory lock test") as other:
            assert other is False

    with locked(path, blocking=False, purpose="advisory lock test") as again:
        assert again is True


def _hold(path: Path) -> int:
    """Open ``path`` so unlink can proceed while this descriptor stays open."""
    return advisory_lock._open_lock(path, removable=True)


def _replace(path: Path) -> int:
    """Return a descriptor for ``path``, then replace or drop that name.

    POSIX can create the replacement while the old descriptor lives. Windows
    keeps the name delete-pending until the last handle closes, and that
    create is access denied.
    """
    stale = _hold(path)
    os.unlink(path)
    try:
        replacement = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    except PermissionError:
        return stale
    os.close(replacement)
    return stale


def test_a_stale_inode_does_not_own_the_replaced_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descriptor opened before unlink must not count as the owner of the new file."""
    path = tmp_path / "nest" / "lock"
    path.parent.mkdir()
    path.write_bytes(b"")
    stale = _replace(path)
    calls = {"n": 0}
    real_open = advisory_lock._open_lock

    def opener(target: Path, *, removable: bool) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return stale
        return real_open(target, removable=removable)

    monkeypatch.setattr(advisory_lock, "_open_lock", opener)
    with locked(path, blocking=False, purpose="advisory lock test", removable=True) as held:
        assert held is True
        with locked(path, blocking=False, purpose="advisory lock test", removable=True) as other:
            assert other is False


def test_a_missing_path_after_open_is_opened_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unlinked name is not owned; the next open creates the file and holds it."""
    path = tmp_path / "nest" / "lock"
    path.parent.mkdir()
    path.write_bytes(b"")
    stale = _hold(path)
    os.unlink(path)
    calls = {"n": 0}
    real_open = advisory_lock._open_lock

    def opener(target: Path, *, removable: bool) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return stale
        return real_open(target, removable=removable)

    monkeypatch.setattr(advisory_lock, "_open_lock", opener)
    with locked(path, blocking=False, purpose="advisory lock test", removable=True) as held:
        assert held is True
        assert path.is_file()
        with locked(path, blocking=False, purpose="advisory lock test", removable=True) as other:
            assert other is False


def test_a_name_that_never_matches_is_not_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A few replaced inodes end as not acquired, and the new file stays free."""
    path = tmp_path / "nest" / "lock"
    path.parent.mkdir()
    path.write_bytes(b"")
    stale = _replace(path)

    def opener(target: Path, *, removable: bool) -> int:
        del target, removable
        return os.dup(stale)

    monkeypatch.setattr(advisory_lock, "_open_lock", opener)
    with locked(path, blocking=False, purpose="advisory lock test", removable=True) as held:
        assert held is False
    os.close(stale)
    monkeypatch.undo()
    with locked(path, blocking=False, purpose="advisory lock test", removable=True) as again:
        assert again is True


def test_an_open_error_that_is_not_a_moved_name_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk error while opening is not reported as a lock someone else holds."""

    def opener(target: Path, *, removable: bool) -> int:
        del target, removable
        raise OSError(errno.EIO, "disk")

    monkeypatch.setattr(advisory_lock, "_open_lock", opener)
    path = tmp_path / "nest" / "lock"
    with pytest.raises(OSError, match="disk"):
        with locked(path, blocking=False, purpose="advisory lock test", removable=True):
            pass
    assert advisory_lock._delete_pending(OSError(errno.EACCES, "posix"), path) is False


def test_windows_access_denied_while_pending_is_not_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ERROR_ACCESS_DENIED`` on a pending name is a moved name, not unavailable.

    On this Windows host an unlink of a delete-shared file removes the name,
    so the next ``OPEN_ALWAYS`` creates a new file. The denial itself is what
    a pending name returns; the opener raises it and the lock is not acquired.
    """
    if sys.platform != "win32":
        return

    def opener(target: Path, *, removable: bool) -> int:
        del removable
        raise OSError(5, "Access is denied", str(target), 5)

    monkeypatch.setattr(advisory_lock, "_open_lock", opener)
    path = tmp_path / "nest" / "lock"
    with locked(path, blocking=False, purpose="advisory lock test", removable=True) as acquired:
        assert acquired is False
