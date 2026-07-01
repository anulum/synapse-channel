# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — git-claim semantic selector UX regressions
"""Regression tests for semantic selector resolution inside git-claim."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.git.gitclaim import run_git_claim


def _write(path: Path, text: str = "") -> None:
    """Write a UTF-8 file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_semantic_repo(root: Path) -> None:
    """Create a minimal repository surface resolvable by semantic claims."""
    _write(
        root / "src" / "synapse_channel" / "core" / "receipts.py",
        """
def build_release_receipt():
    return {}


class ReleaseReceipt:
    pass
""",
    )
    _write(
        root / "tests" / "test_release_receipts.py",
        """
from synapse_channel.core.receipts import ReleaseReceipt, build_release_receipt


def test_receipt():
    assert build_release_receipt() == {}
    assert ReleaseReceipt
""",
    )
    _write(root / "README.md")
    _write(root / "docs" / "_generated" / "capability_manifest.json", "{}\n")
    _write(root / "tools" / "capability_manifest.py")
    _write(root / "tools" / "capability_manifest.toml")
    _write(root / "pyproject.toml")


def _branch_then_repo(branch: str, repo: Path) -> Callable[[list[str]], str]:
    """Return a git runner that answers branch and repository-root queries."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo.as_posix()
        return branch

    return runner


async def test_run_git_claim_resolves_semantic_selectors_into_claim_paths(
    tmp_path: Path,
) -> None:
    _build_semantic_repo(tmp_path)
    evidence = tmp_path / "semantic-evidence.json"

    async with running_hub(SynapseHub()) as (hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="SEMANTIC",
            paths=["docs/manual.md"],
            semantic_selectors=("symbol:synapse_channel.core.receipts.build_release_receipt",),
            semantic_evidence_json=evidence.as_posix(),
            runner=_branch_then_repo("feature/semantic", tmp_path),
        )

    assert rc == 0
    assert hub.state.claims["SEMANTIC"].paths == (
        "docs/manual.md",
        "src/synapse_channel/core/receipts.py",
        "tests/test_release_receipts.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload[0]["selector"] == ("symbol:synapse_channel.core.receipts.build_release_receipt")
    assert payload[0]["claim_paths"] == [
        "src/synapse_channel/core/receipts.py",
        "tests/test_release_receipts.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    ]


async def test_run_git_claim_reports_semantic_selector_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_semantic_repo(tmp_path)

    rc = await run_git_claim(
        uri="ws://127.0.0.1:1",
        name="me",
        task_id="SEMANTIC",
        paths=[],
        semantic_selectors=("module:synapse_channel.missing",),
        runner=_branch_then_repo("feature/semantic", tmp_path),
    )

    assert rc == 1
    assert "semantic claim error: unknown module selector" in capsys.readouterr().out


async def test_run_git_claim_reports_unwritable_semantic_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An evidence destination whose parent is a file fails as an OS error."""
    _build_semantic_repo(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")

    rc = await run_git_claim(
        uri="ws://127.0.0.1:1",
        name="me",
        task_id="SEMANTIC",
        paths=[],
        semantic_selectors=("module:synapse_channel.core.receipts",),
        semantic_evidence_json="blocker/evidence.json",
        runner=_branch_then_repo("feature/semantic", tmp_path),
    )

    assert rc == 1
    assert "semantic claim evidence error:" in capsys.readouterr().out


def test_write_semantic_evidence_resolves_a_relative_destination(tmp_path: Path) -> None:
    """A relative evidence path lands below the repository root."""
    from synapse_channel.git.gitclaim import _write_semantic_evidence

    _write_semantic_evidence((), tmp_path, "sub/evidence.json")
    written = tmp_path / "sub" / "evidence.json"
    assert written.exists()
    assert written.read_text(encoding="utf-8").strip() in ("[]", "{}")
