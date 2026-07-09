# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — team-secure design discoverability tests
"""Guard the team-secure profile docs and public discoverability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEAM_SECURE_DOC = ROOT / "docs" / "team-secure.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_team_secure_design_is_publicly_discoverable() -> None:
    """The design page must be linked from public security and deployment docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    deployment = _read(ROOT / "docs" / "deployment.md")
    security = _read(ROOT / "SECURITY.md")

    assert "Team-secure mode: team-secure.md" in nav
    assert "docs/team-secure.md" in readme
    assert "team-secure.md" in deployment
    assert "docs/team-secure.md" in security


def test_team_secure_design_names_enforced_settings() -> None:
    """The design must name the multi-seat trust gates the profile forces."""
    text = _collapsed(TEAM_SECURE_DOC)

    for setting in (
        "token",
        "identity-trust",
        "role-grants",
        "private directed",
        "require-identity-binding",
        "require-role-claim",
    ):
        assert setting in text


def test_team_secure_design_contrasts_with_paranoid() -> None:
    """The design must distinguish the multi-seat profile from --paranoid."""
    text = _collapsed(TEAM_SECURE_DOC)

    assert "paranoid" in text
    assert "lighter" in text or "lighter than" in text
