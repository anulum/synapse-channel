# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — cross-agent adapter kits design documentation tests
"""Guard the cross-agent adapter kits design boundaries and discoverability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "cross-agent-adapter-kits.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_cross_agent_adapter_kits_is_publicly_discoverable() -> None:
    """The design must be linked from the nav and README."""
    assert "Cross-agent adapter kits: cross-agent-adapter-kits.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/cross-agent-adapter-kits.md" in _read(ROOT / "README.md")


def test_cross_agent_adapter_kits_pins_the_adapter_contract() -> None:
    """The adapter contract and the two adapter shapes must be present."""
    text = _collapsed(DOC)
    assert "claim before edit" in text
    assert "release on commit" in text
    assert "reach the hub" in text
    # the two shapes: native config files and python client shims
    assert "editor and cli agents" in text
    assert "thin client shim" in text
    for tool in ("claude code", "codex", "cursor", "aider"):
        assert tool in text


def test_cross_agent_adapter_kits_grounds_in_shipped_wiring() -> None:
    """The design must build on the shipped wiring, not reinvent it."""
    text = _collapsed(DOC)
    assert "git-init" in text
    assert "worker-session" in text
    assert "synapse adapters list" in text
    assert "synapse adapters install" in text


def test_cross_agent_adapter_kits_credits_prior_art_and_stays_neutral() -> None:
    """Prior art must be attributed and the boundaries must stay persona-neutral."""
    text = _collapsed(DOC)
    assert "agency-agents" in text  # MIT prior art attribution
    assert "mit" in text
    assert "persona-neutral" in text
    assert "not implemented" in text
    assert "reversible" in text
    assert "no new coordination primitive" in text
