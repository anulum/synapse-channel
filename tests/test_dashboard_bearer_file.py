# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a generated dashboard bearer lives in an owner-only file
"""The generated bearer is kept owner-only, replaced whole, and refused anywhere unsafe."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.secure_path import SecurePathError
from synapse_channel.dashboard_bearer_file import (
    BEARER_FILE,
    bearer_directory,
    write_generated_bearer,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_the_directory_follows_xdg_state_home_or_the_home_fallback(tmp_path: Path) -> None:
    assert bearer_directory({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "synapse" / "dashboard"
    fallback = Path.home() / ".local" / "state" / "synapse" / "dashboard"
    assert bearer_directory({"XDG_STATE_HOME": "  "}) == fallback
    assert bearer_directory({}) == fallback


def test_the_bearer_is_written_owner_only_and_a_new_start_replaces_it(tmp_path: Path) -> None:
    directory = tmp_path / "state" / "synapse" / "dashboard"

    first = write_generated_bearer("first-generated-bearer", directory)
    second = write_generated_bearer("second-generated-bearer", directory)

    assert first == second == directory / BEARER_FILE
    assert second.read_text(encoding="utf-8") == "second-generated-bearer\n"
    assert sorted(path.name for path in directory.iterdir()) == [BEARER_FILE]  # nothing staged
    if os.name == "posix":
        assert _mode(directory) == 0o700
        assert _mode(second) == 0o600


def test_the_default_directory_is_the_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert write_generated_bearer("default-place-bearer") == (
        tmp_path / "synapse" / "dashboard" / BEARER_FILE
    )


def test_a_directory_open_to_others_or_a_linked_one_is_refused(tmp_path: Path) -> None:
    open_directory = tmp_path / "open"
    open_directory.mkdir(mode=0o755)
    os.chmod(open_directory, 0o755)
    with pytest.raises(SecurePathError, match="accessible by other users"):
        write_generated_bearer("never-written", open_directory)
    assert list(open_directory.iterdir()) == []

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    os.chmod(real, 0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(SecurePathError, match="is a symlink; refused"):
        write_generated_bearer("never-written", linked)
    assert list(real.iterdir()) == []


def test_a_failed_write_leaves_no_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "dashboard"

    def refuse(_source: object, _target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("synapse_channel.dashboard_bearer_file.os.replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        write_generated_bearer("lost-bearer", directory)

    assert list(directory.iterdir()) == []
