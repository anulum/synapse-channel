# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude Code live-claim guard regressions

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claude_claim_guard import (
    ClaimGuardError,
    RepositoryTarget,
    decide_from_snapshot,
    denial_payload,
    evaluate_hook_event,
)
from synapse_channel.git.semantic_scope import semantic_scope_path


def _event(root: Path, path: Path, *, tool: str = "Edit") -> str:
    return json.dumps(
        {
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
        }
    )


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        if args[-4:] == ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"]:
            return f"{root}\n{branch}"
        assert args[-3:] == ["ls-files", "-z", "--cached"]
        return ""

    return run


def _claim(
    root: Path,
    *,
    owner: str = "seat/one",
    paths: list[str] | None = None,
    branch: str = "main",
    status: str = "claimed",
) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": owner,
        "status": status,
        "worktree": str(root),
        "paths": ["src"] if paths is None else paths,
        "git": {"branch": branch, "base": "main", "auto_release_on": "manual"},
    }


def test_snapshot_allows_only_unambiguous_editable_owner(tmp_path: Path) -> None:
    target = RepositoryTarget(tmp_path.resolve(), "main", "src/package/module.py")
    for paths in (["src"], [], ["src/package/module.py"]):
        verdict = decide_from_snapshot(
            {"active_claims": [_claim(tmp_path, paths=paths)]},
            identity="seat/one",
            target=target,
        )
        assert verdict.allowed


@pytest.mark.parametrize(
    "claim",
    [
        {"owner": "seat/two"},
        {"branch": "other"},
        {"paths": ["tests"]},
        {"status": "input_required"},
    ],
)
def test_snapshot_denies_wrong_owner_scope_branch_or_state(
    tmp_path: Path, claim: dict[str, Any]
) -> None:
    target = RepositoryTarget(tmp_path.resolve(), "main", "src/module.py")
    candidate = _claim(
        tmp_path,
        owner=str(claim.get("owner", "seat/one")),
        paths=claim.get("paths"),
        branch=str(claim.get("branch", "main")),
        status=str(claim.get("status", "claimed")),
    )
    assert not decide_from_snapshot(
        {"active_claims": [candidate]}, identity="seat/one", target=target
    ).allowed


def test_snapshot_denies_competing_covering_claim(tmp_path: Path) -> None:
    target = RepositoryTarget(tmp_path.resolve(), "main", "src/module.py")
    snapshot = {
        "active_claims": [
            _claim(tmp_path, owner="seat/one"),
            _claim(tmp_path, owner="seat/two"),
        ]
    }
    verdict = decide_from_snapshot(snapshot, identity="seat/one", target=target)
    assert not verdict.allowed
    assert "ambiguous" in verdict.reason


def test_snapshot_rejects_malformed_claim_state(tmp_path: Path) -> None:
    target = RepositoryTarget(tmp_path.resolve(), "main", "src/module.py")
    with pytest.raises(ClaimGuardError):
        decide_from_snapshot({"active_claims": "wrong"}, identity="seat/one", target=target)
    bad = _claim(tmp_path)
    bad["paths"] = "src"
    with pytest.raises(ClaimGuardError):
        decide_from_snapshot({"active_claims": [bad]}, identity="seat/one", target=target)
    with pytest.raises(ClaimGuardError):
        decide_from_snapshot({"active_claims": ["bad"]}, identity="seat/one", target=target)


def test_snapshot_denies_missing_or_different_worktree(tmp_path: Path) -> None:
    target = RepositoryTarget(tmp_path.resolve(), "main", "src/module.py")
    missing = _claim(tmp_path)
    missing["worktree"] = ""
    other = _claim(tmp_path)
    other["worktree"] = str(tmp_path / "other")
    for claim in (missing, other):
        verdict = decide_from_snapshot(
            {"active_claims": [claim]}, identity="seat/one", target=target
        )
        assert not verdict.allowed


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_controlled_query_failure(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async def unavailable(**_kwargs: object) -> dict[str, Any]:
        raise ClaimGuardError("hub unavailable")

    verdict = await evaluate_hook_event(
        _event(tmp_path, tmp_path / "src" / "module.py"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=unavailable,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed
    assert verdict.reason == "hub unavailable"


@pytest.mark.asyncio
async def test_evaluate_hook_event_allows_live_covering_claim(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path)]}

    verdict = await evaluate_hook_event(
        _event(tmp_path, tmp_path / "src" / "module.py"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert verdict.allowed


@pytest.mark.asyncio
async def test_only_precise_edit_can_provisionally_use_a_symbol_claim(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    path = tmp_path / "src" / "module.py"
    scope = semantic_scope_path("src/module.py", "run")

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path, paths=[scope])]}

    edit = await evaluate_hook_event(
        _event(tmp_path, path, tool="Edit"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    write = await evaluate_hook_event(
        _event(tmp_path, path, tool="Write"),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert edit.allowed
    assert not write.allowed


@pytest.mark.asyncio
async def test_evaluate_hook_event_denies_malformed_input_before_query() -> None:
    async def must_not_run(**_kwargs: object) -> dict[str, Any]:
        raise AssertionError("malformed input must not query the hub")

    verdict = await evaluate_hook_event(
        "not-json",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=must_not_run,
    )
    assert not verdict.allowed
    assert "not valid JSON" in verdict.reason


def test_denial_payload_uses_current_pretooluse_schema() -> None:
    assert denial_payload("claim required") == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "claim required",
        }
    }
