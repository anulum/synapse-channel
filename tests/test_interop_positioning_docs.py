"""Guard public interoperability positioning against replacement claims."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARISON_DOC = ROOT / "docs" / "comparison.md"
PUBLIC_MARKDOWN = (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")))
PEER_SURFACES = (
    "LangGraph",
    "CrewAI",
    "AutoGen",
    "Copilot",
    "Claude Code",
    "Codex",
    "Cursor",
    "Aider",
)

PEER_PATTERN = "|".join(re.escape(surface) for surface in PEER_SURFACES)
REPLACEMENT_CLAIM = re.compile(
    rf"\b(?:SYNAPSE|Synapse)\s+"
    rf"(?:replaces|supplants|displaces)\s+(?:{PEER_PATTERN})\b"
    rf"|\b(?:replace|supplant|displace)\s+(?:{PEER_PATTERN})\s+"
    rf"(?:with|using)\s+(?:SYNAPSE|Synapse)\b",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    """Read a UTF-8 public documentation file."""
    return path.read_text(encoding="utf-8")


def _collapse_whitespace(text: str) -> str:
    """Normalize Markdown wrapping before checking prose phrases."""
    return " ".join(text.split())


def test_comparison_doc_names_peer_surfaces_without_owning_them() -> None:
    """Comparison docs must name peer tools while placing Synapse beside them."""
    text = _read(COMPARISON_DOC)

    for surface in PEER_SURFACES:
        assert surface in text

    expected_phrases = (
        "not a replacement for orchestration frameworks or coding agents",
        "below and beside",
        "interop surfaces",
    )
    for phrase in expected_phrases:
        assert phrase in text


def test_public_docs_do_not_claim_synapse_replaces_peer_surfaces() -> None:
    """Public docs must not market Synapse as replacing peer agent tools."""
    violations: list[str] = []

    for path in PUBLIC_MARKDOWN:
        text = _read(path)
        for match in REPLACEMENT_CLAIM.finditer(text):
            violations.append(f"{path.relative_to(ROOT)}: {match.group(0)!r}")

    assert violations == []


def test_adapter_docs_position_mcp_and_a2a_as_edges() -> None:
    """MCP and A2A docs must present adapters as edge interop processes."""
    mcp_doc = _collapse_whitespace(_read(ROOT / "docs" / "mcp.md"))
    cli_doc = _collapse_whitespace(_read(ROOT / "docs" / "cli.md"))
    readme = _collapse_whitespace(_read(ROOT / "README.md"))

    assert "separate adapter process, not a hub change" in mcp_doc
    assert "bridge and keeps A2A at the edge of the system" in cli_doc
    assert "adapters are interop surfaces" in readme


def test_comparison_doc_lists_verifiable_differences() -> None:
    """Comparison docs must anchor each differentiator to a real local surface."""
    text = _collapse_whitespace(_read(COMPARISON_DOC))

    expected_pairs = {
        "File-scope claims": "synapse lock",
        "Claim-aware Git hooks": "synapse git-init",
        "Durable event log": "synapse hub --db",
        "Metrics and health endpoints": "synapse hub --metrics",
        "MCP server face": "synapse mcp",
        "A2A bridge": "synapse a2a-card",
        "Release receipts": "synapse release",
        "Local-first operation": "synapse demo",
    }
    for label, command in expected_pairs.items():
        assert label in text
        assert command in text
