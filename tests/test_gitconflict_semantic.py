# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — committed semantic conflict integration
"""Check real branch diffs against claims served by a live local hub."""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from hub_e2e_helpers import close_agents, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.gitconflict import run_conflicts
from test_gitconflict import _claim_live

SOURCE = "class C:\n def first(self):\n  return 1\n def second(self):\n  return 2\n"


def _git(root: Path, *args: str) -> str:
    """Execute Git in the disposable repository with checked failures."""
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise GitError(result.stderr)
    return result.stdout.strip()


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "missing_branch",
        "missing_grammar",
        "ambiguous_base",
        "disjoint_files",
        "no_snapshot",
    ],
)
@pytest.mark.parametrize(
    ("left", "right", "path", "expected"),
    [
        (SOURCE.replace("return 1", "return 3"), SOURCE.replace("return 2", "return 4"), "a.py", 0),
        (SOURCE.replace("return 1", "return 3"), SOURCE.replace("return 1", "return 4"), "a.py", 2),
        (
            SOURCE.replace("class C:", "class C(object):"),
            SOURCE.replace("return 1", "return 4"),
            "a.py",
            2,
        ),
        (
            SOURCE.replace("return 1", "return 4"),
            SOURCE.replace("class C:", "class C(object):"),
            "a.py",
            2,
        ),
        (SOURCE + "\n'\n", SOURCE.replace("return 1", "return 4"), "a.py", 2),
        (
            SOURCE.replace("return 1", "return 3"),
            SOURCE.replace("return 1", "return 4"),
            "odd\nfile.py",
            2,
        ),
        (
            SOURCE.replace("return 1", "return 3"),
            SOURCE.replace("return 1", "return 4"),
            "a.txt",
            2,
        ),
    ],
)
async def test_semantic_prediction_preserves_uncertain_and_nested_overlaps(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    left: str,
    right: str,
    path: str,
    expected: int,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drop only proven disjoint declarations in real committed branch edits."""
    _git(tmp_path, "init", "-q", "--initial-branch=main")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    target = tmp_path / path
    target.write_text(SOURCE)
    (tmp_path / "other.py").write_text(SOURCE)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    for branch, source in [("left", left), ("right", right)]:
        _git(tmp_path, "checkout", "-qb", branch, "main")
        destination = (
            tmp_path / "other.py" if fault == "disjoint_files" and branch == "right" else target
        )
        destination.write_text(source)
        _git(tmp_path, "commit", "-qam", branch)

    if fault == "missing_branch":
        _git(tmp_path, "branch", "-D", "left")
    elif fault == "ambiguous_base":
        left_head = _git(tmp_path, "rev-parse", "left")
        right_head = _git(tmp_path, "rev-parse", "right")
        tree = _git(tmp_path, "rev-parse", "left^{tree}")
        merge_a = _git(
            tmp_path, "commit-tree", tree, "-p", left_head, "-p", right_head, "-m", "merge-a"
        )
        merge_b = _git(
            tmp_path, "commit-tree", tree, "-p", right_head, "-p", left_head, "-m", "merge-b"
        )
        _git(tmp_path, "update-ref", "refs/heads/main", merge_a)
        _git(tmp_path, "update-ref", "refs/heads/left", merge_b)
    elif fault == "missing_grammar":
        real_import = importlib.import_module

        def unavailable(name: str, package: str | None = None) -> ModuleType:
            """Inject a missing installed grammar at the real import boundary."""
            if name == "tree_sitter_python":
                raise ImportError("optional grammar unavailable")
            return real_import(name, package)

        monkeypatch.setattr(importlib, "import_module", unavailable)

    def runner(args: list[str]) -> str:
        """Bind production Git reads to the disposable repository."""
        return _git(tmp_path, *args)

    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "A-task", "left", [path], worktree="/repo-a")
        second = await _claim_live(uri, "B", "B-task", "right", [path], worktree="/repo-b")
        third = await _claim_live(uri, "C", "C-task", "right", [path], worktree="/repo-c")
        try:
            result = await run_conflicts(
                uri=uri,
                name="Reader",
                check_semantic=True,
                repo_root=tmp_path,
                runner=runner,
                attempts=0 if fault == "no_snapshot" else 40,
                check_diff=True,
            )
            if fault == "none":
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "synapse_channel.cli",
                    "conflicts",
                    "--check-semantic",
                    "--check-diff",
                    "--uri",
                    uri,
                    "--name",
                    "CLI-reader",
                    cwd=tmp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
                    assert process.returncode == expected, (stdout, stderr)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
        finally:
            await close_agents(first, second, third)
    result_for_fault = {"none": expected, "disjoint_files": 0, "no_snapshot": 1}
    assert result == result_for_fault.get(fault, 2), capsys.readouterr().out
