# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — differential privacy blackboard design documentation tests
"""Guard the differential-privacy blackboard design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DP_BLACKBOARD_DOC = ROOT / "docs" / "differential-privacy-blackboard.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_differential_privacy_blackboard_design_is_publicly_discoverable() -> None:
    """The blackboard privacy design must be linked from public docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    coordination = _read(ROOT / "docs" / "coordination-model.md")
    private_channels = _read(ROOT / "docs" / "private-channels.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")

    assert "Differential privacy blackboard: differential-privacy-blackboard.md" in nav
    assert "docs/differential-privacy-blackboard.md" in readme
    assert "docs/differential-privacy-blackboard.md" in security
    assert "differential-privacy-blackboard.md" in coordination
    assert "differential-privacy-blackboard.md" in private_channels
    assert "differential-privacy-blackboard.md" in paranoid


def test_differential_privacy_blackboard_design_defines_scope() -> None:
    """The design must define which blackboard data needs privacy treatment."""
    text = _collapsed(DP_BLACKBOARD_DOC)

    required_terms = (
        "multi-organisation blackboard",
        "sensitive progress note",
        "redaction policy",
        "aggregation boundary",
        "noise budget",
        "privacy budget",
        "release receipt",
        "event-log projection",
    )
    for term in required_terms:
        assert term in text


def test_differential_privacy_blackboard_design_defines_controls() -> None:
    """The design must cover redaction, aggregation, noise, and auditing."""
    text = _collapsed(DP_BLACKBOARD_DOC)

    required_controls = (
        "field minimisation",
        "role-based view",
        "cohort threshold",
        "differential privacy",
        "epsilon",
        "delta",
        "privacy ledger",
        "audit trail",
    )
    for control in required_controls:
        assert control in text


def test_differential_privacy_blackboard_design_keeps_boundaries_clear() -> None:
    """The design must not claim privacy controls are implemented today."""
    text = _collapsed(DP_BLACKBOARD_DOC)

    required_boundaries = (
        "design target",
        "not implemented yet",
        "does not encrypt payloads",
        "does not replace private channels",
        "does not replace end-to-end encrypted channels",
        "does not anonymize raw logs",
        "does not authorize board writes",
        "local-first tradeoff",
    )
    for boundary in required_boundaries:
        assert boundary in text
