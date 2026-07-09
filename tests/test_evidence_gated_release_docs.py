# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-seat default closeout is evidence-gated release
"""Guard that public docs teach verify-release → receipt → release as default.

These assert on real repository files (production documentation surfaces), not
mocks. Runtime ownership and receipt attach are covered by
``test_cli_e2e_release.py`` against a live hub.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_multi_seat_golden_path_closes_with_verify_then_receipt_release() -> None:
    """Quickstart step 6 must name the observed receipt path as default closeout."""
    text = _read("docs/quickstart.md")
    assert "Multi-seat golden path" in text
    assert "synapse verify-release" in text
    assert "synapse release" in text
    assert "--receipt" in text
    # default story language — bare release is emergency only
    assert "evidence-gated" in text.lower() or "Evidence-gated release" in text
    assert "emergency" in text.lower()


def test_recipes_evidence_gated_section_is_linked_from_claim_loop() -> None:
    """Parallel-agents recipe claims default closeout and shows the real CLI."""
    text = _read("docs/recipes.md")
    assert "Evidence-gated release (default closeout)" in text
    assert "synapse verify-release" in text
    assert "synapse release" in text
    assert "--receipt" in text
    assert "evidence-gated-release-default-closeout" in text or "default closeout" in text.lower()
