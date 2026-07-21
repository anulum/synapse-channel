# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the coordination specification cannot drift from the code
"""Bind ``docs/coordination-spec.md`` to the implementation it specifies.

A normative document is only as good as its agreement with the code. These
guards fail the build the moment the specification and the implementation
disagree, so the spec cannot quietly rot into fiction:

* every ``tests/...py`` file the spec cites as a pin must exist;
* every invariant the machine-checkable model enforces must be documented; and
* every normative constant the spec publishes must equal the value the code
  actually uses.
"""

from __future__ import annotations

import re
from pathlib import Path

from synapse_channel.connect_failures import NAME_OWNED_CLOSE_CODE
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS,
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
)
from synapse_channel.core.name_ownership import DEFAULT_LEASE_OFFLINE_TTL
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAXIMUM_TTL_SECONDS,
    MINIMUM_TTL_SECONDS,
    SynapseState,
)
from synapse_channel.core.state_resources import DEFAULT_RESOURCE_TTL_SECONDS
from test_coordination_spec_model import MODEL_INVARIANTS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC_PATH = _REPO_ROOT / "docs" / "coordination-spec.md"

_INVARIANT_ID = re.compile(r"INV-[A-Z]{2,3}-\d+")
_TEST_REF = re.compile(r"tests/test_[a-z0-9_]+\.py")


def _spec_text() -> str:
    """Return the specification document text (fail loudly if it is missing)."""
    assert _SPEC_PATH.is_file(), f"missing specification document: {_SPEC_PATH}"
    return _SPEC_PATH.read_text(encoding="utf-8")


def test_every_cited_test_file_exists() -> None:
    """A `Pinned by` reference to a test file that does not exist is drift."""
    refs = sorted(set(_TEST_REF.findall(_spec_text())))
    assert refs, "the spec cites no pinning tests — it is unbound from the suite"
    missing = [ref for ref in refs if not (_REPO_ROOT / ref).is_file()]
    assert not missing, f"spec cites tests that do not exist: {missing}"


def test_model_invariants_are_all_documented() -> None:
    """Every invariant the state model enforces must appear in the spec."""
    documented = set(_INVARIANT_ID.findall(_spec_text()))
    undocumented = sorted(MODEL_INVARIANTS - documented)
    assert not undocumented, f"model enforces undocumented invariants: {undocumented}"


def test_spec_documents_the_model_and_guard() -> None:
    """The spec must point at its machine-checkable model and this drift guard."""
    text = _spec_text()
    assert "tests/test_coordination_spec_model.py" in text
    assert "tests/test_coordination_spec.py" in text


def test_normative_constants_match_the_implementation() -> None:
    """Each published normative constant must equal the value the code uses.

    The spec's *Normative constants* table is the contract; if the code changes
    a value without updating the table (or vice versa), this fails and names the
    row, so the documented budget can never silently diverge from the enforced one.
    """
    text = _spec_text()
    # (row label as it appears in the table, the value string that must be in
    # that row) — the value is read from the live implementation, never hardcoded.
    expected: list[tuple[str, str]] = [
        ("MINIMUM_TTL_SECONDS", str(MINIMUM_TTL_SECONDS)),
        ("MAXIMUM_TTL_SECONDS", str(MAXIMUM_TTL_SECONDS)),
        ("default lease TTL", str(SynapseState().default_ttl_seconds)),
        ("MAX_CLAIMS_PER_AGENT", str(MAX_CLAIMS_PER_AGENT)),
        ("MAX_DECLARED_PATHS", str(MAX_DECLARED_PATHS)),
        ("DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS", str(DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS)),
        ("DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS", str(DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS)),
        ("DEFAULT_LEASE_OFFLINE_TTL", str(DEFAULT_LEASE_OFFLINE_TTL)),
        ("DEFAULT_RESOURCE_TTL_SECONDS", str(DEFAULT_RESOURCE_TTL_SECONDS)),
        ("WIRE_PROTOCOL_VERSION", str(WIRE_PROTOCOL_VERSION)),
        ("NAME_OWNED_CLOSE_CODE", str(NAME_OWNED_CLOSE_CODE)),
    ]
    rows = {
        line.split("|")[1].strip(): line
        for line in text.splitlines()
        if line.count("|") >= 3 and "`" in line
    }
    normalised = {re.sub(r"[`*]", "", label): line for label, line in rows.items()}
    for label, value in expected:
        assert label in normalised, f"spec constants table has no row for {label!r}"
        assert value in normalised[label], (
            f"spec row {label!r} does not carry the implementation value {value!r}"
        )
