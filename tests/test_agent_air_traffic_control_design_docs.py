# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — Agent Air Traffic Control design documentation tests
"""Guard the Agent ATC architecture boundaries and discoverability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "agent-air-traffic-control.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_agent_atc_is_publicly_discoverable() -> None:
    """The architecture must be linked from the nav and README."""
    assert "Agent Air Traffic Control: agent-air-traffic-control.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/agent-air-traffic-control.md" in _read(ROOT / "README.md")


def test_agent_atc_maps_the_control_loop_to_shipped_surfaces() -> None:
    """The control loop must name the real shipped surfaces it composes."""
    text = _collapsed(DOC)
    assert "separation" in text
    assert "merge-risk radar" in text
    assert "evidence-gated completion" in text
    for surface in ("git-claim", "conflicts", "policy-check", "postmortem", "reliability"):
        assert surface in text
    # the memory step references the ingest seam and persistent memory
    assert "ingest" in text
    assert "memory_kinds" in text


def test_agent_atc_cross_links_the_design_set() -> None:
    """ATC must position itself as the composition over the other designs."""
    for sibling in (
        "identity-and-acl.md",
        "signed-events-mtls.md",
        "federated-trust-model.md",
        "agent-trust-graph.md",
    ):
        assert sibling in _read(DOC)


def test_agent_atc_keeps_it_a_coordination_layer_not_a_scheduler() -> None:
    """The boundaries must refuse the orchestrator framing."""
    text = _collapsed(DOC)
    assert "not a scheduler or orchestrator" in text
    assert "only claims gate a mutation" in text or "only a claim gates a mutation" in text
    assert "advisory" in text
    assert "local-first default" in text
    assert "introduces no new trust root" in text
