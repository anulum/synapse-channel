# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — Studio documentation regressions

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "studio.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_studio_doc_is_discoverable() -> None:
    """The Studio page must be in the nav and linked from the README."""
    assert "Studio: studio.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/studio.md" in _read(ROOT / "README.md")


def test_studio_doc_describes_the_design_system_and_reference() -> None:
    """The page must describe the shipped design system and the /studio reference."""
    text = " ".join(_read(DOC).lower().split())
    assert "instrument-panel" in text
    assert "studio.css" in text
    assert "/studio" in text  # the reference page path
    assert "offline" in text  # hub-independent rendering claim
    # the page must be honest about the free/paid boundary
    assert "free" in text and "separate layer" in text
