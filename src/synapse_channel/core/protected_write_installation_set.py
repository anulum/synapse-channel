# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact declared installation artifact closure
"""Capture an independently enrolled artifact set without publishing partial results."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.protected_write_install_artifacts import (
    ProtectedInstallArtifact,
    capture_protected_install_artifact,
)


@dataclass(frozen=True)
class ProtectedInstallArtifactPin:
    """One operator-reviewed exact path, digest and individual read bound.

    Attributes
    ----------
    path : str
        Independently enrolled absolute artifact path, not a caller-selected alias.
    sha256 : str
        Reviewed lowercase SHA-256 of the complete raw file bytes.
    max_bytes : int
        Positive individual capture limit in bytes, at most 64 MiB.
    """

    path: str
    sha256: str
    max_bytes: int


def _validate_artifact_pins(pins: tuple[ProtectedInstallArtifactPin, ...]) -> None:
    """Require a bounded immutable sequence of exact typed artifact declarations."""
    if (
        type(pins) is not tuple
        or not 0 < len(pins) <= 4096
        or any(
            type(pin) is not ProtectedInstallArtifactPin
            or type(pin.path) is not str
            or type(pin.max_bytes) is not int
            or (not 0 < pin.max_bytes <= 64 * 1024 * 1024)
            for pin in pins
        )
    ):
        raise ValueError("invalid installation artifact set enrollment")


def _validate_artifact_set_scope(
    expected_paths: frozenset[str], private_paths: frozenset[str], max_total_bytes: int
) -> None:
    """Keep independent closure, privacy and aggregate capture budgets immutable."""
    if (
        type(expected_paths) is not frozenset
        or not expected_paths
        or any(type(path) is not str for path in expected_paths)
        or (type(private_paths) is not frozenset)
        or (not private_paths <= expected_paths)
        or (type(max_total_bytes) is not int)
        or (not 0 < max_total_bytes <= 1024 * 1024 * 1024)
    ):
        raise ValueError("invalid installation artifact set enrollment")


def capture_protected_install_artifact_set(
    pins: tuple[ProtectedInstallArtifactPin, ...],
    *,
    expected_paths: frozenset[str],
    private_paths: frozenset[str],
    max_total_bytes: int,
) -> tuple[ProtectedInstallArtifact, ...]:
    """Require every independently enrolled artifact with an exact aggregate bound.

    Parameters
    ----------
    pins:
        Immutable reviewed artifact pins. Duplicate paths are refused even when
        they declare the same digest.
    expected_paths:
        Independently retained complete path set from the operator manifest,
        never inferred from the supplied pins.
    private_paths:
        Independently retained credential/config/trust-secret paths within that
        manifest. Pins cannot override their required private permissions.
    max_total_bytes:
        Positive aggregate capture budget, at most 1 GiB, counting every artifact.

    Returns
    -------
    tuple[ProtectedInstallArtifact, ...]
        Actual immutable root-owned artifacts in canonical path order. Any
        refusal returns no partial set and leaves installation state untouched.

    Raises
    ------
    ValueError
        On malformed/duplicate/missing/extra pins, unbound private scope, exhausted
        budget, unsafe root ancestry/artifacts or a digest mismatch.
    OSError
        On inaccessible private bytes, symlinks or unavailable paths/descriptors.

    Notes
    -----
    Run outside the authority actor. This proves the declared set at capture,
    not that a caller's manifest is complete for actual dynamic imports or that
    a sealed installation remains in custody. Manifest approval, closed runtime
    dependency enrollment, supervisor launch and physical isolation are separate
    mandatory prerequisites. This creates no manifest, account, ledger or grant.
    """
    _validate_artifact_pins(pins)
    _validate_artifact_set_scope(expected_paths, private_paths, max_total_bytes)
    paths = tuple(pin.path for pin in pins)
    if len(set(paths)) != len(paths) or frozenset(paths) != expected_paths:
        raise ValueError("installation artifacts differ from exact manifest closure")
    result = []
    remaining = max_total_bytes
    for pin in sorted(pins, key=lambda item: item.path):
        if remaining == 0:
            raise ValueError("installation artifact aggregate budget exhausted")
        artifact = capture_protected_install_artifact(
            pin.path,
            expected_sha256=pin.sha256,
            max_bytes=min(pin.max_bytes, remaining),
            require_private=pin.path in private_paths,
        )
        remaining -= len(artifact.content)
        result.append(artifact)
    return tuple(result)
