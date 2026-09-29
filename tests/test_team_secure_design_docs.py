# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — team-secure design discoverability tests
"""Guard the team-secure profile docs and public discoverability."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from synapse_channel import cli

pytestmark = pytest.mark.docs_contract

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


@pytest.mark.parametrize("doc", ["docs/team-secure.md", "docs/quickstart.md"])
def test_documented_identity_and_role_commands_parse(doc: str) -> None:
    """Every keygen / role-grant example must be accepted by the real parser.

    These pages once showed ``--subject``/``--out-key``/``--enroll`` and a
    positional grantee, none of which the CLI accepts; a copied command failed.
    """
    lines = _read(ROOT / doc).splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip().removeprefix("#").strip()
        if line.startswith(("synapse identity keygen", "synapse role grant")):
            while line.endswith("\\"):
                index += 1
                line = line[:-1] + " " + lines[index].strip().removeprefix("#").strip()
            commands.append(line)
        index += 1
    assert {command.split()[1] for command in commands} == {"identity", "role"}
    for command in commands:
        args = cli.build_parser().parse_args(shlex.split(command)[1:])
        assert callable(args.func), command
