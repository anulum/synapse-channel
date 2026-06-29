# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — multi-hub sync (CRDT) design documentation tests
"""Guard the multi-hub sync research boundaries and discoverability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "multi-hub-sync.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_multi_hub_sync_is_publicly_discoverable() -> None:
    """The research lane must be linked from the nav and README."""
    assert "Multi-hub sync (CRDT) research: multi-hub-sync.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/multi-hub-sync.md" in _read(ROOT / "README.md")


def test_multi_hub_sync_splits_state_by_mergeability() -> None:
    """The design must classify state by what actually merges."""
    text = _collapsed(DOC)
    assert "durable event log" in text
    assert "append-only" in text
    assert "presence" in text
    assert "last-writer-wins" in text or "lww" in text
    assert "vector-clock" in text or "vector clock" in text


def test_multi_hub_sync_refuses_to_treat_claims_as_a_crdt() -> None:
    """The core honest result: claims are mutual exclusion, not a CRDT."""
    text = _collapsed(DOC)
    assert "claims are not a crdt" in text
    assert "mutual exclusion" in text
    assert "namespace ownership" in text or "single-owner-per-namespace" in text
    assert "fail" in text and "closed" in text  # partition fails closed


def test_multi_hub_sync_keeps_local_first_boundaries() -> None:
    """The boundaries must preserve local-first and refuse a global cluster."""
    text = _collapsed(DOC)
    assert "not implemented" in text
    assert "local-first" in text
    assert "no single global leader" in text or "does not introduce a global consensus" in text
    for sibling in ("signed-events-mtls.md", "federated-trust-model.md"):
        assert sibling in _read(DOC)
