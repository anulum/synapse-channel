# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the owner-only directory floor
"""Exercise the owner-only directory floor: creation, clobber refusal, tightening."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.private_dir import (
    PrivateDirError,
    ensure_private_dir,
)

_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="owner-only floor is a POSIX surface")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@_POSIX_ONLY
def test_creates_a_fresh_directory_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    result = ensure_private_dir(target, purpose="fresh runtime")
    assert result == target
    assert target.is_dir()
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_is_idempotent_on_an_existing_owner_only_directory(tmp_path: Path) -> None:
    target = tmp_path / "again"
    ensure_private_dir(target)
    # A second call takes the pre-existing (FileExistsError) validation path.
    assert ensure_private_dir(target) == target
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_retightens_a_loose_but_owned_directory(tmp_path: Path) -> None:
    target = tmp_path / "loose"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    assert _mode(target) == 0o755
    ensure_private_dir(target, purpose="loose runtime")
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_creates_missing_ancestors_when_parents_requested(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "leaf"
    ensure_private_dir(target, parents=True, purpose="nested")
    assert target.is_dir()
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_rejects_a_symlink_at_the_leaf(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PrivateDirError, match="cannot securely open"):
        ensure_private_dir(link, purpose="hijacked link")


@_POSIX_ONLY
def test_rejects_a_regular_file_at_the_path(tmp_path: Path) -> None:
    target = tmp_path / "afile"
    target.write_text("not a dir", encoding="utf-8")
    with pytest.raises(PrivateDirError, match="cannot securely open"):
        ensure_private_dir(target, purpose="file clobber")


@_POSIX_ONLY
def test_rejects_a_foreign_owned_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "foreign"
    target.mkdir(mode=0o700)
    # We own the directory; make the effective user look like someone else so
    # the same-descriptor owner check treats it as foreign-owned.
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(target).st_uid + 1)
    with pytest.raises(PrivateDirError, match="not owned by the effective user"):
        ensure_private_dir(target, purpose="foreign owner")


@_POSIX_ONLY
def test_rejects_a_non_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "realdir"
    target.mkdir(mode=0o700)
    owner = os.stat(target).st_uid
    real_fstat = os.fstat

    def fake_fstat(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        # Present the opened descriptor as a regular file so the ``S_ISDIR``
        # guard fires even where ``O_DIRECTORY`` would otherwise reject it.
        fields = list(info)
        fields[0] = stat.S_IFREG | 0o600
        fields[4] = owner
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", fake_fstat)
    with pytest.raises(PrivateDirError, match="is not a directory"):
        ensure_private_dir(target, purpose="non dir")


@_POSIX_ONLY
def test_raises_when_a_loose_directory_cannot_be_tightened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "stuck"
    target.mkdir(mode=0o777)
    target.chmod(0o777)

    def refuse(fd: int, mode: int) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "fchmod", refuse)
    with pytest.raises(PrivateDirError, match="cannot be tightened"):
        ensure_private_dir(target, purpose="stuck perms")


@_POSIX_ONLY
def test_raises_when_a_directory_stays_loose_after_tightening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "obstinate"
    target.mkdir(mode=0o777)
    target.chmod(0o777)
    # A no-op fchmod leaves the group/other bits set, so the post-tighten
    # re-check must reject rather than trust the write succeeded.
    monkeypatch.setattr(os, "fchmod", lambda fd, mode: None)
    with pytest.raises(PrivateDirError, match="remains accessible"):
        ensure_private_dir(target, purpose="obstinate perms")


@_POSIX_ONLY
def test_reports_when_the_leaf_cannot_be_created(tmp_path: Path) -> None:
    # Parent absent and not requested: the leaf mkdir fails with a non-exists error.
    target = tmp_path / "missing" / "leaf"
    with pytest.raises(PrivateDirError, match="cannot create"):
        ensure_private_dir(target, purpose="orphan leaf")


@_POSIX_ONLY
def test_reports_when_ancestors_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: str, mode: int = 0o777, exist_ok: bool = False) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "makedirs", refuse)
    with pytest.raises(PrivateDirError, match="cannot create parents"):
        ensure_private_dir(tmp_path / "x" / "y" / "z", parents=True, purpose="blocked parents")


def test_refuses_on_a_platform_without_the_posix_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("synapse_channel.core.private_dir._POSIX", False)
    with pytest.raises(PrivateDirError, match="unavailable on this platform"):
        ensure_private_dir(tmp_path / "nope", purpose="no posix")
