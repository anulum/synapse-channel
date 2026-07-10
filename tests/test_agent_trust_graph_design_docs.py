# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — agent trust graph design documentation tests
"""Guard the agent trust graph design boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRUST_GRAPH_DOC = ROOT / "docs" / "agent-trust-graph.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_agent_trust_graph_design_is_publicly_discoverable() -> None:
    """The trust graph design must be linked from public evidence docs."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    security = _read(ROOT / "SECURITY.md")
    coordination = _read(ROOT / "docs" / "coordination-model.md")
    policy = _read(ROOT / "docs" / "policy-engine.md")
    identity = _read(ROOT / "docs" / "identity-and-acl.md")

    assert "Agent trust graph: agent-trust-graph.md" in nav
    assert "docs/agent-trust-graph.md" in readme
    assert "docs/agent-trust-graph.md" in security
    assert "agent-trust-graph.md" in coordination
    assert "agent-trust-graph.md" in policy
    assert "agent-trust-graph.md" in identity


def test_agent_trust_graph_design_defines_evidence_model() -> None:
    """The design must define evidence nodes and provenance boundaries."""
    text = _collapsed(TRUST_GRAPH_DOC)

    required_terms = (
        "agent trust graph",
        "evidence node",
        "evidence edge",
        "provenance reference",
        "event sequence",
        "release receipt",
        "reliability signal",
        "capability observation",
    )
    for term in required_terms:
        assert term in text


def test_agent_trust_graph_design_defines_routing_use() -> None:
    """The design must explain how graph evidence can inform routing."""
    text = _collapsed(TRUST_GRAPH_DOC)

    required_controls = (
        "routing hint",
        "explainable reason",
        "negative evidence",
        "decay window",
        "conflict history",
        "handoff outcome",
        "owner review",
        "policy input",
    )
    for control in required_controls:
        assert control in text


def test_agent_trust_graph_design_keeps_boundaries_clear() -> None:
    """The design must not introduce opaque ranking or trust certification."""
    text = _collapsed(TRUST_GRAPH_DOC)

    required_boundaries = (
        "design target",
        "routing integration and the owner-annotation workflow described below "
        "are not implemented yet",
        "does not rank agents",
        "does not assign trust grades",
        "does not authorize execution",
        "does not replace code review",
        "does not replace identity and acl",
        "local-first tradeoff",
    )
    for boundary in required_boundaries:
        assert boundary in text
