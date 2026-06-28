# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — signed capability card design documentation tests
"""Guard the signed capability card design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNED_CAPABILITY_CARDS_DOC = ROOT / "docs" / "signed-capability-cards.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_signed_capability_cards_design_is_publicly_discoverable() -> None:
    """The signed card design must be linked from public trust docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    protocol = _read(ROOT / "docs" / "protocol.md")
    signed_events = _read(ROOT / "docs" / "signed-events-mtls.md")
    identity_acl = _read(ROOT / "docs" / "identity-and-acl.md")
    encrypted_channels = _read(ROOT / "docs" / "end-to-end-encrypted-channels.md")

    assert "Signed capability cards: signed-capability-cards.md" in nav
    assert "docs/signed-capability-cards.md" in readme
    assert "docs/signed-capability-cards.md" in security
    assert "signed-capability-cards.md" in protocol
    assert "signed-capability-cards.md" in signed_events
    assert "signed-capability-cards.md" in identity_acl
    assert "signed-capability-cards.md" in encrypted_channels


def test_signed_capability_cards_design_defines_signing_profile() -> None:
    """The design must define how card signatures bind advertisements."""
    text = _collapsed(SIGNED_CAPABILITY_CARDS_DOC)

    required_terms = (
        "signed capability card",
        "canonical card",
        "card signature",
        "key id",
        "agent binding",
        "manifest digest",
        "verification result",
        "tamper evidence",
    )
    for term in required_terms:
        assert term in text


def test_signed_capability_cards_design_defines_lifecycle_controls() -> None:
    """The design must cover replay, rotation, and downgrade controls."""
    text = _collapsed(SIGNED_CAPABILITY_CARDS_DOC)

    required_controls = (
        "sequence binding",
        "timestamp window",
        "replay protection",
        "credential rotation",
        "revocation",
        "trust bundle",
        "capability downgrade",
        "expiry",
    )
    for control in required_controls:
        assert control in text


def test_signed_capability_cards_design_keeps_boundaries_clear() -> None:
    """The design must not claim card verification is implemented today."""
    text = _collapsed(SIGNED_CAPABILITY_CARDS_DOC)

    required_boundaries = (
        "design target",
        "not implemented yet",
        "does not authorize tools",
        "does not replace per-message authentication",
        "does not replace signed events",
        "does not sandbox agents",
        "local-first tradeoff",
        "advisory discovery",
    )
    for boundary in required_boundaries:
        assert boundary in text
