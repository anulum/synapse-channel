# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the operations guide states where a catalogue run's authority stops
"""The operations guide says that a local catalogue run is not a governed operation of the hub."""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _collapsed(page: str) -> str:
    """Return one guide page with its whitespace collapsed."""
    return " ".join((DOCS / page).read_text(encoding="utf-8").split())


def test_the_guide_says_a_local_run_is_outside_the_hubs_governed_operations() -> None:
    """A reader learns that the hub neither records nor admits a local run."""
    text = _collapsed("operations.md")

    assert "A run like this is outside the hub's governed operations." in text
    assert "The hub holds no declaration, approval, attempt or outcome for it" in text
    assert "an approval on the hub neither admits it nor is needed for it" in text


def test_the_guide_points_to_a_governed_path_that_exists() -> None:
    """The linked integrator contract holds the steps the guide names."""
    text = _collapsed("operations.md")
    contract = _collapsed("integrator-contract.md")

    assert "[integrator contract](integrator-contract.md)" in text
    for step in ("synapse operation declare", "synapse operation attempt", "--intent-sha256"):
        assert step in contract, step
