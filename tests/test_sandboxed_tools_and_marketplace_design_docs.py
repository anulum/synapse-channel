# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — sandboxed tools and marketplace design documentation tests
"""Guard the sandboxed-tools and marketplace research boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "sandboxed-tools-and-marketplace.md"


def _read(path: Path) -> str:
    """Read a UTF-8 documentation file."""
    return path.read_text(encoding="utf-8")


def _collapsed(path: Path) -> str:
    """Return lowercase documentation text with normalized whitespace."""
    return " ".join(_read(path).lower().split())


def test_sandboxed_tools_is_publicly_discoverable() -> None:
    """The research lane must be linked from the nav and README."""
    nav = _read(ROOT / "mkdocs.yml")
    assert "Sandboxed tools and marketplace: sandboxed-tools-and-marketplace.md" in nav
    assert "docs/sandboxed-tools-and-marketplace.md" in _read(ROOT / "README.md")


def test_sandbox_is_capability_limited_and_deny_by_default() -> None:
    """The sandbox must remove ambient authority and gate every capability."""
    text = _collapsed(DOC)
    assert "webassembly" in text
    assert "no ambient authority" in text
    assert "deny-by-default" in text
    for capability in ("filesystem", "network", "resources"):
        assert capability in text


def test_marketplace_requires_the_preconditions_first() -> None:
    """A marketplace is gated behind sandbox, signing, permissions, and receipts."""
    text = _collapsed(DOC)
    assert "no marketplace before the preconditions" in text
    assert "signed capability card" in text
    assert "permission manifest" in text
    assert "run receipt" in text


def test_sandboxed_tools_keeps_one_authorization_path_and_local_first() -> None:
    """The boundaries must reuse the ACL model and preserve local-first."""
    text = _collapsed(DOC)
    assert "not implemented" in text
    assert "no parallel authorisation path" in text or "one deny-by-default model" in text
    assert "local-first" in text
    for sibling in ("signed-capability-cards.md", "identity-and-acl.md"):
        assert sibling in _read(DOC)
