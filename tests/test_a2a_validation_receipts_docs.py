# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — A2A validation receipts documentation regressions

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "a2a-validation-receipts.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_validation_receipts_doc_is_discoverable() -> None:
    """The receipts template must be in the nav and linked from the README."""
    assert "A2A validation receipts: a2a-validation-receipts.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/a2a-validation-receipts.md" in _read(ROOT / "README.md")


def test_validation_receipts_doc_lists_every_receipt_and_credits_the_contributor() -> None:
    """The six receipts, the framing, and the community attribution must be present."""
    text = " ".join(_read(DOC).lower().split())
    for receipt in (
        "discovery receipt",
        "task-lifecycle receipt",
        "webhook receipt",
        "proxy / tls receipt",
        "replay / subscription receipt",
        "threat-model receipt",
    ):
        assert receipt in text
    # the framing: receipts not a single pass/fail, and protocol vs operational safety
    assert "set of receipts that survive across the bridge boundary" in text
    assert "separate protocol compatibility from operational safety" in text
    # the key edge case and the community attribution
    assert "restart + bounded replay + a real webhook receiver" in text
    assert "armorer labs" in text
    assert "discussions/20" in text
