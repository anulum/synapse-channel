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

import pytest

pytestmark = pytest.mark.docs_contract

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


def test_sandbox_documents_granted_limits_and_ambient_exceptions() -> None:
    """The sandbox must expose its actual grant and containment boundaries."""
    text = _collapsed(DOC)
    assert "webassembly" in text
    assert "only manifest-selected preopens" in text
    assert "network grants are recorded as policy but do not enable networking" in text
    assert "standard wasi clock imports remain available without a manifest grant" in text
    assert "does not authenticate an operator" in text
    assert "direct callers of `run_sandboxed` supply their own digest and approval checks" in text
    for capability in ("filesystem", "network", "resources"):
        assert capability in text


def test_marketplace_admission_is_proposed_separately_from_runtime_facilities() -> None:
    """Separate existing facilities must not be claimed as composed admission."""
    text = _collapsed(DOC)
    assert "marketplace distribution layer is not implemented" in text
    assert "not yet composed into a marketplace install or execution admission path" in text
    assert "accepted signed card binding declared capabilities and module content" in text
    assert "expiry and revocation enforced at admission" in text
    assert "authenticated caller permissions" in text
    assert "module digest checks, runtime limits and authenticated run evidence" in text
    assert "these checks are separate today" in text


def test_sandboxed_tools_preserves_separate_authority_and_local_first_boundaries() -> None:
    """Sandbox confirmation and coordination ACLs have separate contracts."""
    text = _collapsed(DOC)
    assert "not implemented" in text
    assert "sandbox cli does not run enrolled-agent acl checks" in text
    assert "requires `--approve` as local caller confirmation" in text
    assert "valid card signature" in text
    assert "does not prove module behaviour or grant execution authority" in text
    assert "local-first" in text
    for sibling in ("signed-capability-cards.md", "identity-and-acl.md"):
        assert sibling in _read(DOC)
