# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real declared artifact closure verification tests
"""Read real root-owned artifacts without equating OS examples with a writer install."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.protected_write_installation_set import (
    ProtectedInstallArtifactPin,
    capture_protected_install_artifact_set,
)
from test_protected_write_install_artifacts import native_installation_paths


def native_pins() -> tuple[ProtectedInstallArtifactPin, ...]:
    """Pin actual root-owned OS files for artifact-set mechanics only."""
    return tuple(
        ProtectedInstallArtifactPin(
            path, hashlib.sha256(Path(path).read_bytes()).hexdigest(), Path(path).stat().st_size
        )
        for path in map(str, native_installation_paths())
    )


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "mutable_pins",
        "pin_type",
        "path_type",
        "pin_budget",
        "too_many",
        "mutable_expected",
        "empty_expected",
        "expected_type",
        "mutable_private",
        "foreign_private",
        "boolean_budget",
        "zero_budget",
        "oversize_budget",
    ],
)
def test_invalid_set_enrollment_refuses(tmp_path: Path, case: str) -> None:
    """Invalid immutable scope/budget shape refuses before artifact capture."""
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"declared bytes")
    pins: tuple[ProtectedInstallArtifactPin, ...] = (
        ProtectedInstallArtifactPin(
            str(artifact),
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
            artifact.stat().st_size,
        ),
    )
    expected = frozenset(pin.path for pin in pins)
    private: frozenset[str] = frozenset()
    budget = 4096
    if case == "empty":
        pins = ()
    elif case == "mutable_pins":
        pins = cast(tuple[ProtectedInstallArtifactPin, ...], list(pins))
    elif case == "pin_type":
        pins = cast(tuple[ProtectedInstallArtifactPin, ...], ("bad",))
    elif case == "path_type":
        pins = (replace(pins[0], path=cast(str, 1)),)
    elif case == "pin_budget":
        pins = (replace(pins[0], max_bytes=0),)
    elif case == "too_many":
        pins = (pins[0],) * 4097
    elif case == "mutable_expected":
        expected = cast(frozenset[str], set(expected))
    elif case == "empty_expected":
        expected = frozenset()
    elif case == "expected_type":
        expected = cast(frozenset[str], frozenset({1}))
    elif case == "mutable_private":
        private = cast(frozenset[str], set())
    elif case == "foreign_private":
        private = frozenset({"/unenrolled"})
    elif case == "boolean_budget":
        budget = True
    elif case == "zero_budget":
        budget = 0
    else:
        budget = 1024 * 1024 * 1024 + 1
    with pytest.raises(ValueError, match="set enrollment"):
        capture_protected_install_artifact_set(
            pins, expected_paths=expected, private_paths=private, max_total_bytes=budget
        )
