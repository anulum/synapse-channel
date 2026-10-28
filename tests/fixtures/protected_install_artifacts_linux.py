# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native root installation artifact verification tests
"""Capture actual root-owned files and refuse agent-controlled installation trees."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from synapse_channel.core.protected_write_install_artifacts import (
    capture_protected_install_artifact,
)
from test_protected_write_install_artifacts import native_installation_paths


@pytest.mark.parametrize("index", [0, 1], ids=["configuration", "executable"])
def test_actual_root_artifact_preserves_reviewed_bytes_and_inode(index: int) -> None:
    """Small config and a multichunk executable retain actual root inode and digest."""
    path = str(native_installation_paths()[index])
    original = Path(path).read_bytes()
    expected = hashlib.sha256(original).hexdigest()
    artifact = capture_protected_install_artifact(
        path, expected_sha256=expected, max_bytes=len(original), require_private=False
    )
    assert artifact.content == original and artifact.sha256 == expected
    info = Path(path).stat()
    assert (artifact.device, artifact.inode, artifact.mode) == (
        info.st_dev,
        info.st_ino,
        info.st_mode & 0o7777,
    )
    assert "content=" not in repr(artifact)


def test_agent_owned_tree_never_supplies_installation_bytes(tmp_path: Path) -> None:
    """Even matching bytes cannot establish root-owned ancestry from this checkout UID."""
    path = tmp_path / "artifact"
    path.write_bytes(b"exact")
    with pytest.raises(ValueError, match="ancestry"):
        capture_protected_install_artifact(
            str(path),
            expected_sha256=hashlib.sha256(b"exact").hexdigest(),
            max_bytes=1024,
            require_private=False,
        )


def test_native_artifact_read_budget_is_enforced() -> None:
    """An actual root-owned executable cannot overrun a bounded artifact read."""
    with pytest.raises(ValueError, match="read budget"):
        capture_protected_install_artifact(
            str(native_installation_paths()[1]),
            expected_sha256="0" * 64,
            max_bytes=1,
            require_private=False,
        )


def test_root_owned_directory_is_not_an_executable_artifact() -> None:
    """Root ownership cannot turn an actual directory into a regular install file."""
    with pytest.raises(ValueError, match="regular file"):
        capture_protected_install_artifact(
            "/usr/bin",
            expected_sha256="0" * 64,
            max_bytes=1024,
            require_private=False,
        )


def test_actual_symlink_is_not_a_pinned_artifact() -> None:
    """An actual root-owned symlink cannot replace an exact enrolled inode path."""
    alias = Path("/bin")
    assert alias.is_symlink(), "the native root fixture requires its actual /bin alias"
    with pytest.raises(OSError):
        capture_protected_install_artifact(
            str(alias),
            expected_sha256="0" * 64,
            max_bytes=1024,
            require_private=False,
        )


def test_public_root_configuration_cannot_be_called_a_private_credential() -> None:
    """A real world-readable root file fails the independent privacy requirement."""
    with pytest.raises(ValueError, match="another principal"):
        capture_protected_install_artifact(
            str(native_installation_paths()[0]),
            expected_sha256="0" * 64,
            max_bytes=1024,
            require_private=True,
        )


def test_native_artifact_rejects_a_changed_reviewed_digest() -> None:
    """Genuine root-owned bytes still need their exact reviewed digest."""
    with pytest.raises(ValueError, match="reviewed digest"):
        capture_protected_install_artifact(
            str(native_installation_paths()[0]),
            expected_sha256="0" * 64,
            max_bytes=1024,
            require_private=False,
        )
