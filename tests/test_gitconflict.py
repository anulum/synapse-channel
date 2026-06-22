# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for branch-scoped merge-conflict prediction

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from synapse_channel.gitclaim import AgentFactory, GitError
from synapse_channel.gitconflict import (
    PredictedConflict,
    _overlap,
    branch_diff_files,
    find_conflicts,
    run_conflicts,
)


class FakeAgent:
    """A SynapseAgent stand-in that replays an inbound state snapshot."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self._ready = ready
        self._inbound = inbound or []
        self.state_requests = 0

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_state(self) -> None:
        self.state_requests += 1


def make_factory(
    *, ready: bool = True, inbound: list[dict[str, Any]] | None = None
) -> tuple[AgentFactory, list[FakeAgent]]:
    created: list[FakeAgent] = []

    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, ready=ready, inbound=inbound, **kwargs)
        created.append(agent)
        return agent

    return cast(AgentFactory, factory), created


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
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
    ]
    factory, _created = make_factory(
        inbound=[{"type": "chat", "payload": "noise"}, _snapshot(claims)]
    )
    rc = await run_conflicts(uri="ws://t", name="U", agent_factory=factory, runner=lambda _a: "")
    assert rc == 2
    out = capsys.readouterr().out
    assert "Predicted conflicts (1)" in out
    assert "A@feature/x" in out


async def test_run_conflicts_none(capsys: pytest.CaptureFixture[str]) -> None:
    claims = [_claim("T1", "A", "feature/x", ["src/auth.py"])]
    factory, _created = make_factory(inbound=[_snapshot(claims)])
    rc = await run_conflicts(uri="ws://t", name="U", agent_factory=factory, runner=lambda _a: "")
    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out


async def test_run_conflicts_unreachable() -> None:
    factory, _created = make_factory(ready=False)
    rc = await run_conflicts(uri="ws://t", name="U", agent_factory=factory, runner=lambda _a: "")
    assert rc == 1


async def test_run_conflicts_check_diff_refines(capsys: pytest.CaptureFixture[str]) -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
    ]
    factory, _created = make_factory(inbound=[_snapshot(claims)])
    # Neither branch actually changed the file -> the predicted conflict is dropped.
    rc = await run_conflicts(
        uri="ws://t",
        name="U",
        check_diff=True,
        agent_factory=factory,
        runner=lambda _a: "unrelated.py\n",
    )
    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out


async def test_run_conflicts_check_diff_keeps_real_overlap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
    ]
    factory, _created = make_factory(inbound=[_snapshot(claims)])
    rc = await run_conflicts(
        uri="ws://t",
        name="U",
        check_diff=True,
        agent_factory=factory,
        runner=lambda _a: "src/auth.py\n",
    )
    assert rc == 2
    assert "Predicted conflicts (1)" in capsys.readouterr().out


async def test_run_conflicts_check_diff_keeps_when_diff_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
    ]
    factory, _created = make_factory(inbound=[_snapshot(claims)])

    def bad_runner(_args: list[str]) -> str:
        raise GitError("branch not checked out locally")

    # A branch whose diff cannot be computed is kept unrefined — better to over-warn.
    rc = await run_conflicts(
        uri="ws://t", name="U", check_diff=True, agent_factory=factory, runner=bad_runner
    )
    assert rc == 2
    assert "Predicted conflicts (1)" in capsys.readouterr().out


async def test_run_conflicts_check_diff_caches_repeated_branches(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return "src/auth.py\n"

    claims = [
        _claim("T1", "A", "feature/x", ["src/auth.py"]),
        _claim("T2", "B", "feature/y", ["src/auth.py"]),
        _claim("T3", "C", "feature/z", ["src/auth.py"]),
    ]
    factory, _created = make_factory(inbound=[_snapshot(claims)])
    rc = await run_conflicts(
        uri="ws://t", name="U", check_diff=True, agent_factory=factory, runner=runner
    )
    assert rc == 2
    assert "Predicted conflicts (3)" in capsys.readouterr().out
    # Three branches, each diffed once despite appearing in two pairs (cache hit).
    assert len(calls) == 3


async def test_run_conflicts_without_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.gitconflict.asyncio.sleep", no_sleep)
    factory, _created = make_factory(inbound=[])
    rc = await run_conflicts(uri="ws://t", name="U", agent_factory=factory, runner=lambda _a: "")
    assert rc == 0
    assert "No predicted conflicts." in capsys.readouterr().out
