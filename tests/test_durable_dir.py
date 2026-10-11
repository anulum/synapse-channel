# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for owner-only directories with synced creation
"""Real directories. What a sync makes durable cannot be observed without a
power cut; these tests show what is created, what is refused, and that the
sync calls succeed on real directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from synapse_channel.core.private_dir import PrivateDirError
from synapse_channel.durable_dir import private_dir, sync_directory


def test_every_missing_directory_is_made_owner_only_leaf(tmp_path: Path) -> None:
    previous = os.umask(0)
    try:
        leaf = tmp_path / "a" / "b" / "c"
        private_dir(leaf, purpose="test directory")
        private_dir(leaf, purpose="test directory")
    finally:
        os.umask(previous)

    assert leaf.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(leaf.stat().st_mode) == 0o700
    sync_directory(leaf)
    sync_directory(tmp_path)


def test_a_path_through_a_file_and_a_foreign_kind_are_refused(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(PrivateDirError, match="test directory"):
        private_dir(blocker / "below", purpose="test directory")
    if os.name == "posix":
        link = tmp_path / "link"
        link.symlink_to(tmp_path / "elsewhere")
        with pytest.raises(PrivateDirError):
            private_dir(link, purpose="test directory")
        with pytest.raises(OSError):
            sync_directory(tmp_path / "absent")
