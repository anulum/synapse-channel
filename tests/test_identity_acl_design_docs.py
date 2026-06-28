# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — identity and ACL design documentation tests
"""Guard the per-agent identity and ACL design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ACL_DOC = ROOT / "docs" / "identity-and-acl.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_identity_acl_design_is_publicly_discoverable() -> None:
    """The identity and ACL design must be linked from public security docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    paranoid = _read(ROOT / "docs" / "paranoid-mode.md")
    per_message = _read(ROOT / "docs" / "per-message-authentication.md")
    signed_events = _read(ROOT / "docs" / "signed-events-mtls.md")
    private_channels = _read(ROOT / "docs" / "private-channels.md")

    assert "Identity and ACL: identity-and-acl.md" in nav
    assert "docs/identity-and-acl.md" in readme
    assert "docs/identity-and-acl.md" in security
    assert "identity-and-acl.md" in paranoid
    assert "identity-and-acl.md" in per_message
    assert "identity-and-acl.md" in signed_events
    assert "identity-and-acl.md" in private_channels


def test_identity_acl_design_defines_identity_model() -> None:
    """The design must define the first identity-bound credential model."""
    text = _collapsed(IDENTITY_ACL_DOC)

    required_terms = (
        "per-agent identity",
        "identity-bound credential",
        "agent id",
        "seat id",
        "project namespace",
        "credential rotation",
        "revocation",
        "audit subject",
    )
    for term in required_terms:
        assert term in text


def test_identity_acl_design_defines_acl_model() -> None:
    """The design must define the permission vocabulary and default posture."""
    text = _collapsed(IDENTITY_ACL_DOC)

    required_permissions = (
        "allowed verb",
        "target pattern",
        "metrics permission",
        "a2a permission",
        "dashboard permission",
        "release permission",
        "namespace permission",
        "deny by default",
    )
    for permission in required_permissions:
        assert permission in text


def test_identity_acl_design_keeps_migration_and_boundaries_clear() -> None:
    """The design must not claim authorization exists before implementation."""
    text = _collapsed(IDENTITY_ACL_DOC)

    required_boundaries = (
        "design target",
        "not implemented yet",
        "shared-token mode",
        "migration",
        "does not replace per-message authentication",
        "does not replace signed events",
        "does not sandbox agents",
        "local-first tradeoff",
    )
    for boundary in required_boundaries:
        assert boundary in text
