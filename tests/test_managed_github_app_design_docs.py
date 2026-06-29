# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — managed GitHub App design documentation tests
"""Guard the managed GitHub App design's boundary and not-implemented status."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "managed-github-app.md"


def _collapsed() -> str:
    """Return lowercase doc text with normalized whitespace."""
    return " ".join(DOC.read_text(encoding="utf-8").lower().split())


def test_design_is_in_the_nav() -> None:
    assert "Managed GitHub App (design): managed-github-app.md" in (
        ROOT / "mkdocs.yml"
    ).read_text(encoding="utf-8")


def test_design_states_it_is_not_implemented_and_gated() -> None:
    text = _collapsed()
    assert "not implemented" in text
    assert "adoption signal" in text or "gated on adoption" in text


def test_design_keeps_the_core_managed_boundary() -> None:
    text = _collapsed()
    # the prediction reuses the existing local-core conflict finder
    assert "find_conflicts" in text
    assert "local core" in text and "managed layer" in text
    # the check is advisory, never a merge gate
    assert "advisory" in text
    # no GitHub/hosting dependency leaks into the local core
    assert "never adds github" in text or "no managed concern leaks" in text
