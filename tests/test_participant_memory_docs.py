# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Participant memory documentation contract tests
"""Guard Participant memory discoverability, flags, and honesty boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "participant-memory.md"


def _read(path: Path) -> str:
    """Read one UTF-8 documentation surface."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_participant_memory_guide_is_publicly_discoverable() -> None:
    """The implementation guide must be linked from every primary entry point."""
    assert "Participant memory recall: participant-memory.md" in _read(ROOT / "mkdocs.yml")
    assert "docs/participant-memory.md" in _read(ROOT / "README.md")
    assert "participant-memory.md" in _read(ROOT / "docs" / "cli.md")


def test_participant_memory_guide_documents_every_cli_flag_and_default() -> None:
    """Operators need the complete opt-in configuration contract in one page."""
    text = _collapsed(DOC)
    for flag in (
        "--memory-url",
        "--memory-token-file",
        "--memory-timeout",
        "--memory-top-k",
        "--memory-max-chars",
    ):
        assert flag in text
    for default in ("`2`", "`3`", "`4096`"):
        assert default in text
    for command in ("participant ask", "participant exchange", "participant convene"):
        assert command in text


def test_participant_memory_guide_preserves_security_and_honesty_boundaries() -> None:
    """The guide must not promote recalled data or weaken secret handling."""
    text = _collapsed(DOC)
    for boundary in (
        "recall is off",
        "no token-literal option exists",
        "refuses redirects",
        "rejects cleartext http outside a literal loopback ip",
        "boundary",
        "does not certify that the content is true",
        "status: abstained",
        "status: unavailable",
        "never calls `/remember`",
        "operator prompt",
    ):
        assert boundary in text


def test_participant_memory_guide_states_the_actual_audit_boundary() -> None:
    """The stdlib metadata audit must stay distinct from MCP query telemetry."""
    text = _collapsed(DOC)
    assert "metadata-only request audit rows" in text
    assert "does not record the query body" in text
    assert "query-stream recall telemetry is a separate remanentia mcp behavior" in text
    assert "does not emit query text to the synapse event log" in text
