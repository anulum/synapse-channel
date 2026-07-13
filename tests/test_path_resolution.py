# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed weak path resolution tests
"""Focused tests for version-independent weak path resolution."""

from pathlib import Path

import pytest

from synapse_channel.path_resolution import PathResolutionError, resolve_weakly_fail_closed


def test_existing_and_missing_paths_are_canonicalised(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()

    assert resolve_weakly_fail_closed(existing) == existing
    assert resolve_weakly_fail_closed(existing / "new" / "file.txt") == (
        existing / "new" / "file.txt"
    )
    assert resolve_weakly_fail_closed(existing / ".." / "sibling.txt") == (tmp_path / "sibling.txt")


def test_relative_path_uses_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_weakly_fail_closed(Path("new/file.txt")) == tmp_path / "new" / "file.txt"


def test_valid_symlink_is_resolved_before_a_missing_tail(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert resolve_weakly_fail_closed(link / "new.txt") == target / "new.txt"


def test_broken_symlink_is_rejected(tmp_path: Path) -> None:
    link = tmp_path / "broken"
    link.symlink_to(tmp_path / "missing")

    with pytest.raises(PathResolutionError, match="invalid symbolic link"):
        resolve_weakly_fail_closed(link / "file.txt")


def test_symlink_loop_is_rejected_at_any_existing_prefix(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    with pytest.raises(PathResolutionError, match="invalid symbolic link"):
        resolve_weakly_fail_closed(loop)
    with pytest.raises(PathResolutionError, match="invalid symbolic link"):
        resolve_weakly_fail_closed(tmp_path / "missing" / ".." / "loop" / "file.txt")


def test_non_directory_existing_prefix_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(PathResolutionError, match="unreadable component"):
        resolve_weakly_fail_closed(file_path / "child")
