"""Guard the policy-engine design page and its public boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_DOC = ROOT / "docs" / "policy-engine.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return a documentation file with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_policy_engine_design_is_publicly_discoverable() -> None:
    """The design page must be in nav and linked from the public entry points."""
    nav = _read(ROOT / "mkdocs.yml")
    readme = _read(ROOT / "README.md")
    index = _read(ROOT / "docs" / "index.md")

    assert "Policy engine: policy-engine.md" in nav
    assert "policy-engine.md" in readme
    assert "policy-engine.md" in index


def test_policy_engine_design_covers_enterprise_rules() -> None:
    """The policy design must name the first rule families explicitly."""
    text = _collapsed(POLICY_DOC)

    required_phrases = (
        "required tests",
        "strict type checking",
        "owner approval",
        "evidence freshness",
        "no-merge-without-receipt",
        "claim coverage",
        "generated artifact parity",
        "known-failure acknowledgement",
    )
    for phrase in required_phrases:
        assert phrase in text


def test_policy_engine_design_keeps_enforcement_boundaries_clear() -> None:
    """The design must not claim hidden or current automatic merge authority."""
    text = _collapsed(POLICY_DOC)

    required_boundaries = (
        "advisory by default",
        "does not merge code",
        "does not replace code review",
        "does not call external policy services",
        "local-first",
        "future enforcement mode",
    )
    for phrase in required_boundaries:
        assert phrase in text


def test_policy_engine_design_wires_to_existing_receipt_surfaces() -> None:
    """The design must build on existing release receipts and event-log evidence."""
    text = _collapsed(POLICY_DOC)

    required_surfaces = (
        "synapse release",
        "release receipt",
        "synapse event-query",
        "synapse postmortem",
        "synapse reliability",
        "git hooks",
    )
    for surface in required_surfaces:
        assert surface in text
