# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — validation of the fixtures' capability-capable Bubblewrap
"""Accept only a root-owned Bubblewrap path that no other account can change."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from fixtures.capable_bwrap import (
    DEFAULT,
    ENVIRONMENT,
    capable_bwrap,
    capable_bwrap_path,
    require_trusted_directory,
    validated_capable_bwrap,
)

ROOT = Path(__file__).resolve().parents[1]


def _root_owned_executable() -> str:
    """Return a real root-owned executable outside any writable directory."""
    shell = shutil.which("sh")
    assert shell is not None
    return os.path.realpath(shell)


def test_the_variable_selects_the_path_and_its_absence_the_default() -> None:
    """Only the CI runner names another Bubblewrap; the default is the system one."""
    assert capable_bwrap_path({}) == DEFAULT == "/usr/bin/bwrap"
    assert capable_bwrap_path({ENVIRONMENT: "/opt/bwrap"}) == "/opt/bwrap"


def test_a_root_owned_executable_in_root_owned_directories_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real root-owned file with only root-owned, non-writable parents passes."""
    executable = _root_owned_executable()
    assert validated_capable_bwrap(executable) == executable
    monkeypatch.setenv(ENVIRONMENT, executable)
    assert capable_bwrap() == executable


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("usr/bin/bwrap", "must be absolute and normalised"),
        ("/usr/../usr/bin/bwrap", "must be absolute and normalised"),
        ("/usr//bin/bwrap", "must be absolute and normalised"),
    ],
)
def test_relative_or_unnormalised_paths_are_refused(value: str, message: str) -> None:
    """A path whose parents differ from its spelling cannot be checked, so it is refused."""
    with pytest.raises(ValueError, match=message):
        validated_capable_bwrap(value)


def test_missing_link_directory_writable_and_foreign_files_are_refused(tmp_path: Path) -> None:
    """Each unsafe leaf is refused for its own reason, before its parents are read."""
    with pytest.raises(ValueError, match="does not exist"):
        validated_capable_bwrap(str(tmp_path / "absent"))
    link = tmp_path / "link"
    link.symlink_to(_root_owned_executable())
    with pytest.raises(ValueError, match="is a symbolic link"):
        validated_capable_bwrap(str(link))
    with pytest.raises(ValueError, match="is not a regular file"):
        validated_capable_bwrap(str(tmp_path))
    writable = tmp_path / "writable"
    writable.write_bytes(b"")
    writable.chmod(0o777)
    with pytest.raises(ValueError, match="is group- or world-writable"):
        validated_capable_bwrap(str(writable))
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"")
    foreign.chmod(0o755)
    assert foreign.stat().st_uid != 0
    with pytest.raises(ValueError, match="is not root-owned"):
        validated_capable_bwrap(str(foreign))


def test_parents_must_be_root_owned_directories_that_only_root_can_change(
    tmp_path: Path,
) -> None:
    """A world-writable, foreign or non-directory parent is refused."""
    shared = Path(os.path.realpath("/tmp"))
    assert shared.stat().st_uid == 0 and shared.stat().st_mode & 0o002
    with pytest.raises(ValueError, match="group- or world-writable"):
        require_trusted_directory(shared)
    assert tmp_path.stat().st_uid != 0
    with pytest.raises(ValueError, match="not root-owned"):
        require_trusted_directory(tmp_path)
    leaf = tmp_path / "leaf"
    leaf.write_bytes(b"")
    with pytest.raises(ValueError, match="not a directory"):
        require_trusted_directory(leaf)
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="not a directory"):
        require_trusted_directory(link)


def test_only_the_test_helper_reads_the_capability_variable() -> None:
    """Product code never sees the CI test capability, and no other test reads it."""
    readers = sorted(
        str(path.relative_to(ROOT))
        for top in ("src", "tests", "tools")
        for path in (ROOT / top).rglob("*.py")
        if ENVIRONMENT in path.read_text(encoding="utf-8")
    )
    assert readers == ["tests/fixtures/capable_bwrap.py"]
