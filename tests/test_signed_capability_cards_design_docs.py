# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — signed capability card documentation tests
"""Guard the signed capability card runtime and its honest boundaries."""

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


def test_signed_capability_cards_are_publicly_discoverable() -> None:
    """The signed card runtime must be linked from public trust docs."""
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


def test_signed_capability_cards_define_signing_profile() -> None:
    """The public guide must define how signatures bind advertisements."""
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


def test_signed_capability_cards_define_runtime_and_lifecycle_controls() -> None:
    """The guide must cover shipped verbs, replay, rotation, and downgrade."""
    text = _collapsed(SIGNED_CAPABILITY_CARDS_DOC)

    runtime_surfaces = (
        "synapse capability-card keygen",
        "synapse capability-card sign",
        "synapse capability-card verify",
        "synapse worker --capability-card-key",
        "synapse hub --capability-card-trust",
        "--capability-card-history-db",
    )
    for surface in runtime_surfaces:
        assert surface in text

    required_controls = (
        "sequence",
        "validity window",
        "replay",
        "credential rotation",
        "revocation",
        "trust bundle",
        "capability downgrade",
        "expiry",
        "history_unavailable",
        "cross-restart",
    )
    for control in required_controls:
        assert control in text


def test_signed_capability_cards_keep_boundaries_clear() -> None:
    """The guide must separate advisory verification from authority."""
    text = _collapsed(SIGNED_CAPABILITY_CARDS_DOC)

    required_boundaries = (
        "implemented as advisory tamper evidence",
        "do not authorize tools",
        "replace message authentication",
        "replace signed events",
        "sandbox agents",
        "no enforcement flag exists yet",
        "in-memory default",
        "recovery",
        "advisory discovery",
    )
    for boundary in required_boundaries:
        assert boundary in text
