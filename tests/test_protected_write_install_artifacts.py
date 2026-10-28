# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native root installation artifact verification tests
"""Capture actual root-owned files and refuse agent-controlled installation trees."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.protected_write_install_artifacts import (
    capture_protected_install_artifact,
)


def native_installation_paths() -> tuple[Path, Path]:
    """Return exact public root-owned POSIX configuration and executable examples."""
    return Path("/etc/shells").resolve(strict=True), Path("/bin/bash").resolve(strict=True)


@pytest.mark.parametrize(
    "path",
    [
        "relative",
        "/",
        "/etc//debian_version",
        "/etc/../debian_version",
        "/etc/./debian_version",
        "/etc/\x00bad",
        "/" + "x" * 4096,
    ],
)
def test_malformed_installation_path_refuses(path: str) -> None:
    """Invalid enrollment refuses before touching any path."""
    with pytest.raises(ValueError, match="enrollment"):
        capture_protected_install_artifact(
            path, expected_sha256="0" * 64, max_bytes=1024, require_private=False
        )


@pytest.mark.parametrize("budget", [True, 0, -1, 64 * 1024 * 1024 + 1])
def test_invalid_installation_budget_refuses(budget: int) -> None:
    """No bool, disabled or unbounded read budget becomes an enrollment."""
    with pytest.raises(ValueError, match="enrollment"):
        capture_protected_install_artifact(
            "/etc/debian_version",
            expected_sha256="0" * 64,
            max_bytes=budget,
            require_private=False,
        )


@pytest.mark.parametrize("digest", ["", "A" * 64, "0" * 63, "g" * 64])
def test_malformed_reviewed_digest_refuses(digest: str) -> None:
    """Digest enrollment requires exact lowercase SHA256 representation."""
    with pytest.raises(ValueError, match="enrollment"):
        capture_protected_install_artifact(
            "/etc/debian_version", expected_sha256=digest, max_bytes=1024, require_private=False
        )


def test_nonboolean_private_requirement_refuses() -> None:
    """A numeric privacy flag cannot silently select a weaker artifact policy."""
    with pytest.raises(ValueError, match="enrollment"):
        capture_protected_install_artifact(
            "/etc/debian_version",
            expected_sha256="0" * 64,
            max_bytes=1024,
            require_private=cast(bool, 0),
        )


def test_missing_posix_nofollow_support_refuses_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modeled missing native descriptor support cannot select a weaker opener."""
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(OSError, match="requires POSIX"):
        capture_protected_install_artifact(
            "/enrolled", expected_sha256="0" * 64, max_bytes=1, require_private=False
        )
