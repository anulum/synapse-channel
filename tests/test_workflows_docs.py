# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — declarative workflow documentation tests
"""Guard the declarative-workflow docs: discoverability and the board boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "workflows.md"


def _collapsed() -> str:
    """Return lowercase doc text with normalized whitespace."""
    return " ".join(DOC.read_text(encoding="utf-8").lower().split())


def test_workflows_doc_is_in_the_nav() -> None:
    assert "Declarative workflows: workflows.md" in (ROOT / "mkdocs.yml").read_text(
        encoding="utf-8"
    )


def test_workflows_doc_documents_the_cli_and_format() -> None:
    text = _collapsed()
    assert "synapse workflow validate" in text
    assert "synapse workflow compile" in text
    for field in ("depends_on", "task_class", "steps"):
        assert field in text


def test_workflows_doc_keeps_the_blackboard_boundary() -> None:
    text = _collapsed()
    assert "the blackboard is the executor" in text
    assert "no scheduler" in text
    assert "single-dependency" in text
    # strict validation (cycle rejection) is documented
    assert "cycle" in text


def test_workflows_doc_documents_proof_carrying_steps() -> None:
    text = _collapsed()
    assert "evidence requirements" in text
    assert "requires" in text
    assert (
        "synapse workflow plan release.json --status status.json --evidence evidence.json" in text
    )
    for predicate in ("receipt", "policy", "approval", "sandbox_run", "dead_letters"):
        assert predicate in text
