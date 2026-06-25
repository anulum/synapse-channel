# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for branch-scoped merge-conflict prediction

from __future__ import annotations

from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.gitconflict import (
    PredictedConflict,
    _overlap,
    branch_diff_files,
    find_conflicts,
    run_conflicts,
)


def _claim(
    task_id: str, owner: str, branch: str, paths: list[str], base: str = "main"
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "owner": owner,
        "paths": paths,
        "git": {"branch": branch, "base": base, "auto_release_on": "merge"},
    }


def _snapshot(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "state_snapshot", "snapshot": {"active_claims": claims}}


async def _claim_live(
    uri: str,
    name: str,
    task_id: str,
    branch: str,
    paths: list[str],
    *,
    worktree: str,
) -> AgentHandle:
    handle = await connect_agent(name, uri)
    await handle.agent.claim(
        task_id,
        worktree=worktree,
        paths=paths,
        git={"branch": branch, "base": "main", "auto_release_on": "merge"},
    )
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == MessageType.CLAIM_GRANTED and message.get("task_id") == task_id
        )
    )
    return handle


# -- _overlap -----------------------------------------------------------------


def test_overlap_whole_worktree() -> None:
    assert _overlap([], []) == []
    assert _overlap([], ["b"]) == ["b"]
    assert _overlap(["a"], []) == ["a"]


def test_overlap_exact_prefix_and_miss() -> None:
    assert _overlap(["src/a.py"], ["src/a.py"]) == ["src/a.py"]
    assert _overlap(["src"], ["src/a.py"]) == ["src/a.py"]
    assert _overlap(["src/a.py"], ["docs"]) == []


# -- find_conflicts -----------------------------------------------------------


def test_find_conflicts_cross_branch_overlap() -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
    ]
    conflicts = find_conflicts(claims)
    assert len(conflicts) == 1
    assert conflicts[0].owner_a == "A"
    assert conflicts[0].branch_b == "feature/y"
    assert conflicts[0].paths == ("src/auth.py",)


def test_find_conflicts_same_branch_is_ignored() -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/x", ["src/auth.py"]),
    ]
    assert find_conflicts(claims) == []


def test_find_conflicts_no_path_overlap() -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["docs/guide.md"]),
    ]
    assert find_conflicts(claims) == []


def test_find_conflicts_skips_claims_without_git() -> None:
    claims: list[dict[str, Any]] = [
        {"task_id": "T1", "owner": "A", "paths": ["src"], "git": None},
        {"task_id": "T2", "owner": "B", "paths": ["src"], "git": {"branch": ""}},
        _claim("T3", "C", "feature/z", ["src"]),
    ]
    assert find_conflicts(claims) == []  # only one git-scoped claim remains


def test_find_conflicts_whole_worktree_pair() -> None:
    claims = [
        _claim("T1", "A", "feature/x", []),
        _claim("T2", "B", "feature/y", []),
    ]
    conflicts = find_conflicts(claims)
    assert len(conflicts) == 1
    assert conflicts[0].paths == ()


def test_find_conflicts_tolerates_none_paths() -> None:
    # A claim carrying an explicit None scope is treated as the whole worktree, not a crash.
    claims: list[dict[str, Any]] = [
        {"git": {"branch": "x"}, "paths": None, "owner": "A"},
        {"git": {"branch": "y"}, "paths": None, "owner": "B"},
    ]
    assert len(find_conflicts(claims)) == 1


# -- describe -----------------------------------------------------------------


def test_describe_with_paths() -> None:
    conflict = PredictedConflict("A", "x", "main", "B", "y", "main", ("src/a.py",))
    line = conflict.describe()
    assert "A@x" in line
    assert "B@y" in line
    assert "src/a.py" in line


def test_describe_whole_worktree() -> None:
    conflict = PredictedConflict("A", "x", "main", "B", "y", "main", ())
    assert "the whole worktree" in conflict.describe()


# -- branch_diff_files --------------------------------------------------------


def test_branch_diff_files_uses_three_dot_range() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "src/a.py\nsrc/b.py\n"

    assert branch_diff_files("feature/x", "main", runner=runner) == ["src/a.py", "src/b.py"]
    assert captured == [["diff", "--name-only", "main...feature/x"]]


# -- run_conflicts ------------------------------------------------------------


async def test_run_conflicts_reports_predictions(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        second = await _claim_live(uri, "B", "T2", "feature/y", ["src/auth.py"], worktree="/repo-b")
        try:
            rc = await run_conflicts(uri=uri, name="U", runner=lambda _a: "")
        finally:
            await close_agents(first, second)

    assert rc == 2
    out = capsys.readouterr().out
    assert "Predicted conflicts (1)" in out
    assert "A@feature/x" in out


async def test_run_conflicts_none(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        handle = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        try:
            rc = await run_conflicts(uri=uri, name="U", runner=lambda _a: "")
        finally:
            await close_agents(handle)

    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out


async def test_run_conflicts_unreachable() -> None:
    rc = await run_conflicts(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="U",
        runner=lambda _a: "",
        ready_timeout=0.1,
        attempts=1,
    )
    assert rc == 1


async def test_run_conflicts_check_diff_refines(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        second = await _claim_live(uri, "B", "T2", "feature/y", ["src/auth.py"], worktree="/repo-b")
        try:
            rc = await run_conflicts(
                uri=uri,
                name="U",
                check_diff=True,
                runner=lambda _a: "unrelated.py\n",
            )
        finally:
            await close_agents(first, second)

    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out


async def test_run_conflicts_check_diff_keeps_real_overlap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        second = await _claim_live(uri, "B", "T2", "feature/y", ["src/auth.py"], worktree="/repo-b")
        try:
            rc = await run_conflicts(
                uri=uri,
                name="U",
                check_diff=True,
                runner=lambda _a: "src/auth.py\n",
            )
        finally:
            await close_agents(first, second)

    assert rc == 2
    assert "Predicted conflicts (1)" in capsys.readouterr().out


async def test_run_conflicts_check_diff_keeps_when_diff_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def bad_runner(_args: list[str]) -> str:
        raise GitError("branch not checked out locally")

    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        second = await _claim_live(uri, "B", "T2", "feature/y", ["src/auth.py"], worktree="/repo-b")
        try:
            rc = await run_conflicts(uri=uri, name="U", check_diff=True, runner=bad_runner)
        finally:
            await close_agents(first, second)

    assert rc == 2
    assert "Predicted conflicts (1)" in capsys.readouterr().out


async def test_run_conflicts_check_diff_caches_repeated_branches(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return "src/auth.py\n"

    async with running_hub(SynapseHub()) as (_hub, uri):
        first = await _claim_live(uri, "A", "T1", "feature/x", ["src/auth.py"], worktree="/repo-a")
        second = await _claim_live(uri, "B", "T2", "feature/y", ["src/auth.py"], worktree="/repo-b")
        third = await _claim_live(uri, "C", "T3", "feature/z", ["src/auth.py"], worktree="/repo-c")
        try:
            rc = await run_conflicts(uri=uri, name="U", check_diff=True, runner=runner)
        finally:
            await close_agents(first, second, third)

    assert rc == 2
    assert "Predicted conflicts (3)" in capsys.readouterr().out
    # Three branches, each diffed once despite appearing in two pairs (cache hit).
    assert len(calls) == 3


async def test_run_conflicts_empty_live_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        rc = await run_conflicts(uri=uri, name="U", runner=lambda _a: "")

    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out
