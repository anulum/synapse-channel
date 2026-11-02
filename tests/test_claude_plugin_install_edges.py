# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude plugin lifecycle edges: oversized marker, failed removal
"""An oversized marker is never trusted; a removal that fails puts the remainder back."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.claude_plugin_install import (
    ClaudePluginInstallError,
    apply_plugin,
    inspect_plugin,
    plugin_path,
)


def _install(root: Path) -> Path:
    binary = shutil.which("synapse")
    assert binary is not None
    installed = apply_plugin(
        "install",
        config_root=root,
        identity="project/claude",
        uri="ws://localhost:8876",
        synapse_bin=binary,
    )
    assert installed.state == "owned"
    return plugin_path(root)


def _entries(target: Path) -> set[str]:
    return {str(path.relative_to(target)) for path in target.rglob("*")}


def test_an_oversized_marker_makes_the_plugin_foreign(tmp_path: Path) -> None:
    """A marker of exactly 64 KiB is parsed; one byte more is foreign and blocks removal."""
    root = tmp_path / "profile"
    target = _install(root)
    marker = target / ".synapse-install.json"
    encoded = json.dumps(json.loads(marker.read_text(encoding="utf-8")))
    marker.write_text(encoded.ljust(65_536), encoding="utf-8")
    assert marker.stat().st_size == 65_536
    assert inspect_plugin(root).state == "owned"
    marker.write_text(encoded.ljust(65_537), encoding="utf-8")
    assert marker.stat().st_size == 65_537
    assert inspect_plugin(root).state == "foreign"
    with pytest.raises(ClaudePluginInstallError, match="foreign or modified"):
        apply_plugin("uninstall", config_root=root)
    assert marker.is_file()


@pytest.fixture
def read_only_hooks(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """An owned plugin whose hooks directory its owner cannot write into."""
    root = tmp_path / "profile"
    target = _install(root)
    hooks = target / "hooks"
    hooks.chmod(0o500)
    try:
        yield root, target
    finally:
        for directory in (hooks, *(root / "skills").glob(".synapse-removal-*/hooks")):
            directory.chmod(0o700)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX directory permissions that bind the current user",
)
def test_a_failed_removal_restores_the_remainder_and_leaves_no_tombstone(
    read_only_hooks: tuple[Path, Path],
) -> None:
    """An undeletable file stops the uninstall; what is left returns to the plugin path."""
    root, target = read_only_hooks
    before = _entries(target)
    assert inspect_plugin(root).state == "owned"
    with pytest.raises(PermissionError):
        apply_plugin("uninstall", config_root=root)
    assert [path.name for path in (root / "skills").iterdir()] == ["synapse-channel"]
    remainder = _entries(target)
    assert {"hooks", "hooks/hooks.json"} <= remainder <= before
    assert inspect_plugin(root).state != "absent"
