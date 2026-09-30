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
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.git.gitclaim import run_git_claim
from synapse_channel.git.semantic_scope import semantic_scope_path


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
    """Return a runner for branch, repository-root, and index-spelling queries."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo.as_posix()
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return branch
        if args == ["rev-parse", "--git-path", "hooks"]:
            return (repo / ".git" / "hooks").as_posix()
        if args[-3:] == ["ls-files", "-z", "--cached"]:
            paths = sorted(
                path.relative_to(repo).as_posix()
                for path in repo.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(repo).parts
            )
            return "".join(f"{path}\0" for path in paths)
        raise AssertionError(args)

    return runner


def _commit_repo(root: Path) -> str:
    """Initialise ``root`` and return its first commit hash."""
    commands = (
        ("init", "-q"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "."),
        ("commit", "-qm", "base"),
    )
    for command in commands:
        subprocess.run(["git", "-C", str(root), *command], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
        "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt",
        "tests/test_release_receipts.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload[0]["selector"] == ("symbol:synapse_channel.core.receipts.build_release_receipt")
    assert payload[0]["claim_paths"] == [
        "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt",
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
    out = capsys.readouterr().out
    assert "semantic claim error: could not resolve the requested semantic scope" in out


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


async def test_run_git_claim_resolves_selectors_without_evidence_output(
    tmp_path: Path,
) -> None:
    """Selectors resolve into claim paths even when no evidence file is requested."""
    _build_semantic_repo(tmp_path)

    async with running_hub(SynapseHub()) as (hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="SEMANTIC",
            paths=[],
            semantic_selectors=("symbol:synapse_channel.core.receipts.build_release_receipt",),
            semantic_evidence_json=None,
            runner=_branch_then_repo("feature/semantic", tmp_path),
        )

    assert rc == 0
    assert (
        "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt"
        in hub.state.claims["SEMANTIC"].paths
    )
    assert not (tmp_path / "semantic-evidence.json").exists()


async def test_run_git_claim_infers_worktree_diff_and_writes_combined_evidence(
    tmp_path: Path,
) -> None:
    _build_semantic_repo(tmp_path)
    base = _commit_repo(tmp_path)
    source = tmp_path / "src" / "synapse_channel" / "core" / "receipts.py"
    source.write_text(
        "\ndef build_release_receipt():\n    return {'changed': True}\n\n\n"
        "class ReleaseReceipt:\n    pass\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "diff-evidence.json"

    async with running_hub(SynapseHub()) as (hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="diff-agent",
            task_id="DIFF",
            paths=[],
            semantic_diff_base=base,
            semantic_diff_paths=("src/synapse_channel/core/receipts.py",),
            semantic_evidence_json=evidence.as_posix(),
            runner=_branch_then_repo("feature/diff", tmp_path),
        )

    assert rc == 0
    claim_paths = hub.state.claims["DIFF"].paths
    assert claim_paths[0] == (
        "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt"
    )
    assert "tests/test_release_receipts.py" in claim_paths
    assert "docs/_generated/capability_manifest.json" in claim_paths
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload == [
        {
            "base": base,
            "claim_paths": list(claim_paths),
            "companion_claim_paths": list(claim_paths[1:]),
            "head": None,
            "kind": "diff-summary",
            "note": "tree-sitter evidence; incomplete mappings widen to whole-file claims",
            "records": [
                {
                    "claim_paths": [claim_paths[0]],
                    "kind": "diff",
                    "language": "python",
                    "narrowed": True,
                    "old_source": "src/synapse_channel/core/receipts.py",
                    "reason": "all changed lines map to named declarations",
                    "semantic_scopes": [claim_paths[0]],
                    "source": "src/synapse_channel/core/receipts.py",
                    "status": "M",
                    "symbols": ["build_release_receipt"],
                }
            ],
        }
    ]


async def test_synthetic_symbol_paths_coexist_but_whole_file_is_refused(
    tmp_path: Path,
) -> None:
    _build_semantic_repo(tmp_path)
    runner = _branch_then_repo("feature/symbols", tmp_path)
    source = "src/synapse_channel/core/receipts.py"
    first = semantic_scope_path(source, "build_release_receipt")
    second = semantic_scope_path(source, "ReleaseReceipt")

    async with running_hub(SynapseHub()) as (hub, uri):
        first_rc = await run_git_claim(
            uri=uri,
            name="first",
            task_id="FIRST",
            paths=[first],
            runner=runner,
        )
        second_rc = await run_git_claim(
            uri=uri,
            name="second",
            task_id="SECOND",
            paths=[second],
            runner=runner,
        )
        whole_rc = await run_git_claim(
            uri=uri,
            name="whole",
            task_id="WHOLE",
            paths=[source],
            runner=runner,
        )

    assert first_rc == second_rc == 0
    assert whole_rc == 1
    assert set(hub.state.claims) == {"FIRST", "SECOND"}


@pytest.mark.parametrize("field", ["owner", "task_id"])
async def test_run_git_claim_ignores_foreign_grants_and_explains_silence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    """A real transport grant for another owner or task never authorizes edits."""

    def foreign(data: dict[str, object]) -> None:
        """Corrupt one grant field while confirmation replies are lost."""
        if data.get("type") == "claim_granted":
            data[field] = "someone-else"

    async with running_hub() as (_hub, upstream):
        proxy = ClaimProxy(upstream, drop_grants=False, drop_snapshots=True, transform=foreign)
        async with claim_proxy(proxy) as uri:
            rc = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T",
                paths=["src"],
                runner=_branch_then_repo("feature/x", tmp_path),
                attempts=2,
                poll_interval=0.01,
            )
    assert rc == 3
    assert "claim outcome unknown for 'T'" in capsys.readouterr().out
