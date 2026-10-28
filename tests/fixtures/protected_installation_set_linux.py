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

import pytest

from synapse_channel.core.protected_write_installation_set import (
    capture_protected_install_artifact_set,
)
from test_protected_write_installation_set import native_pins


def test_exact_native_set_has_canonical_order_and_bytes() -> None:
    """Every independently named real file is returned in deterministic path order."""
    pins = native_pins()
    artifacts = capture_protected_install_artifact_set(
        pins,
        expected_paths=frozenset(pin.path for pin in pins),
        private_paths=frozenset(),
        max_total_bytes=sum(pin.max_bytes for pin in pins),
    )
    assert tuple(item.path for item in artifacts) == tuple(sorted(pin.path for pin in pins))
    for artifact in artifacts:
        assert artifact.content == Path(artifact.path).read_bytes()
        assert artifact.sha256 == hashlib.sha256(artifact.content).hexdigest()


@pytest.mark.parametrize("case", ["missing", "extra", "duplicate"])
def test_exact_manifest_scope_cannot_be_inferred_from_supplied_pins(case: str) -> None:
    """The independent path set rejects omissions, additions and duplicate entries."""
    pins = native_pins()
    expected = frozenset(pin.path for pin in pins)
    if case == "missing":
        pins = pins[:1]
    elif case == "extra":
        expected = frozenset({pins[0].path})
    else:
        pins = (*pins, pins[0])
    with pytest.raises(ValueError, match="exact manifest closure"):
        capture_protected_install_artifact_set(
            pins, expected_paths=expected, private_paths=frozenset(), max_total_bytes=4096
        )


@pytest.mark.parametrize("remaining", [0, 1])
def test_aggregate_capture_bound_never_returns_a_partial_set(remaining: int) -> None:
    """Real first-file capture leaves insufficient budget for the actual second file."""
    pins = tuple(sorted(native_pins(), key=lambda item: item.path))
    with pytest.raises(ValueError, match="budget"):
        capture_protected_install_artifact_set(
            pins,
            expected_paths=frozenset(pin.path for pin in pins),
            private_paths=frozenset(),
            max_total_bytes=pins[0].max_bytes + remaining,
        )


def test_private_scope_is_independent_of_public_file_pin() -> None:
    """A valid public root-file digest cannot weaken its enrolled credential privacy."""
    pins = native_pins()
    with pytest.raises(ValueError, match="another principal"):
        capture_protected_install_artifact_set(
            pins,
            expected_paths=frozenset(pin.path for pin in pins),
            private_paths=frozenset({pins[0].path}),
            max_total_bytes=sum(pin.max_bytes for pin in pins),
        )


def test_changed_native_artifact_digest_refuses_entire_set() -> None:
    """Actual root-owned bytes still refuse a changed independent second-file pin."""
    pins = tuple(sorted(native_pins(), key=lambda item: item.path))
    pins = (pins[0], replace(pins[1], sha256="0" * 64))
    with pytest.raises(ValueError, match="reviewed digest"):
        capture_protected_install_artifact_set(
            pins,
            expected_paths=frozenset(pin.path for pin in pins),
            private_paths=frozenset(),
            max_total_bytes=sum(pin.max_bytes for pin in pins),
        )
