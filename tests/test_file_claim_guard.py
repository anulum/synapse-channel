# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral file claim guard regressions

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run
from synapse_channel.claim_state import ClaimStateError
from synapse_channel.file_claim_guard import (
    MutationRequest,
    RepositoryTarget,
    decide_targets_from_snapshot,
    evaluate_mutation_request,
    requester_name,
    resolve_repository_targets,
)
from synapse_channel.git.path_identity import resolve_claim_scope_identity
from synapse_channel.git.semantic_scope import semantic_scope_path


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        if "ls-files" in args:
            return ""
        if "core.ignorecase" in args:
            return "false"
        return f"{root}\n{branch}"

    return run


def _claim(root: Path, paths: list[str]) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": "seat/one",
        "status": "claimed",
        "worktree": str(root),
        "paths": paths,
        "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
    }


def test_multi_target_resolution_deduplicates_and_requires_every_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    request = MutationRequest(
        session_id="session",
        tool_use_id="tool",
        cwd=tmp_path,
        file_paths=(Path("src/a.py"), Path("src/b.py"), Path("./src/a.py")),
    )
    targets = resolve_repository_targets(request, provider="Provider", runner=_runner(tmp_path))
    assert [target.relative_path for target in targets] == ["src/a.py", "src/b.py"]

    allowed = decide_targets_from_snapshot(
        {"active_claims": [_claim(tmp_path, ["src/a.py", "src/b.py"])]},
        identity="seat/one",
        targets=targets,
    )
    assert allowed.allowed

    denied = decide_targets_from_snapshot(
        {"active_claims": [_claim(tmp_path, ["src/a.py"])]},
        identity="seat/one",
        targets=targets,
    )
    assert not denied.allowed
    assert "src/b.py" in denied.reason
    assert denied.reason_code == "GUARD_NO_CLAIM"


def test_real_hardlink_target_requires_its_own_display_claim(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    owned = repo / "owned.py"
    alias = repo / "alias.py"
    owned.write_text("VALUE = 1\n", encoding="utf-8")
    alias.hardlink_to(owned)
    git_run(repo, "add", "owned.py", "alias.py")
    git_run(repo, "commit", "-q", "-m", "hardlink fixture")
    root, displays, identity = resolve_claim_scope_identity(repo, ["owned.py"])
    claim = _claim(root, [displays[0]])
    claim["path_identity"] = identity.as_dict()
    request = MutationRequest("session", "tool", root, (Path("alias.py"),))

    targets = resolve_repository_targets(request, provider="Provider")
    verdict = decide_targets_from_snapshot(
        {"active_claims": [claim]},
        identity="seat/one",
        targets=targets,
    )

    assert not verdict.allowed
    assert "alias.py" in verdict.reason


def test_resolution_rejects_empty_paths_and_relative_cwd(tmp_path: Path) -> None:
    empty = MutationRequest("session", "tool", tmp_path, ())
    with pytest.raises(RuntimeError, match="no mutation paths"):
        resolve_repository_targets(empty, provider="Provider", runner=_runner(tmp_path))

    relative = MutationRequest("session", "tool", Path("relative"), (Path("file.py"),))
    with pytest.raises(RuntimeError, match="cwd must be absolute"):
        resolve_repository_targets(relative, provider="Provider", runner=_runner(tmp_path))

    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    invalid = MutationRequest("session", "tool", tmp_path, (Path("loop/file.py"),))
    with pytest.raises(RuntimeError, match="not a valid path"):
        resolve_repository_targets(invalid, provider="Provider", runner=_runner(tmp_path))


def test_precise_edit_can_use_one_unambiguous_symbol_claim(tmp_path: Path) -> None:
    source = "src/a.py"
    target = RepositoryTarget(tmp_path.resolve(), "main", source)
    owner_scope = semantic_scope_path(source, "owned")
    other_scope = semantic_scope_path(source, "other")

    denied = decide_targets_from_snapshot(
        {"active_claims": [_claim(tmp_path, [owner_scope])]},
        identity="seat/one",
        targets=(target,),
    )
    assert not denied.allowed
    assert "claim required" in denied.reason
    assert denied.reason_code == "GUARD_NO_CLAIM"

    allowed = decide_targets_from_snapshot(
        {"active_claims": [_claim(tmp_path, [owner_scope])]},
        identity="seat/one",
        targets=(target,),
        allow_semantic_source=True,
    )
    assert allowed.allowed

    competing = _claim(tmp_path, [other_scope])
    competing["owner"] = "seat/two"
    ambiguous = decide_targets_from_snapshot(
        {"active_claims": [_claim(tmp_path, [owner_scope]), competing]},
        identity="seat/one",
        targets=(target,),
        allow_semantic_source=True,
    )
    assert not ambiguous.allowed
    assert "ambiguous" in ambiguous.reason
    assert ambiguous.reason_code == "GUARD_OWNERSHIP_AMBIGUOUS"


def test_requester_pool_is_stable_and_bounded(tmp_path: Path) -> None:
    names = {
        requester_name(MutationRequest("session", f"tool-{index}", tmp_path, (Path("a"),)), "owner")
        for index in range(100)
    }
    assert len(names) <= 16
    request = MutationRequest("session", "tool", tmp_path, (Path("a"),))
    assert requester_name(request, "owner") == requester_name(request, "owner")
    assert all(name.startswith("claim-hook/") for name in names)


@pytest.mark.asyncio
async def test_evaluation_converts_authoritative_state_failure_to_denial(tmp_path: Path) -> None:
    recorded: list[dict[str, Any]] = []

    async def unavailable(**_kwargs: object) -> dict[str, Any]:
        raise ClaimStateError("hub unavailable")

    async def record(**kwargs: Any) -> bool:
        recorded.append(kwargs)
        return True

    verdict = await evaluate_mutation_request(
        MutationRequest("session", "tool", tmp_path, (Path("new.py"),)),
        provider="Provider",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=unavailable,
        git_runner=_runner(tmp_path),
        denial_recorder=record,
    )
    assert not verdict.allowed
    assert verdict.reason == "hub unavailable"
    assert verdict.reason_code == "GUARD_STATE_UNREACHABLE"
    assert recorded[0]["reason_code"] == "GUARD_STATE_UNREACHABLE"
    assert recorded[0]["paths"] == ("new.py",)


@pytest.mark.asyncio
async def test_evaluation_propagates_precise_semantic_permission(tmp_path: Path) -> None:
    source = "src/a.py"
    scope = semantic_scope_path(source, "run")

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(tmp_path, [scope])]}

    verdict = await evaluate_mutation_request(
        MutationRequest(
            "session",
            "tool",
            tmp_path,
            (Path(source),),
            allow_semantic_source=True,
        ),
        provider="Provider",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
    )
    assert verdict.allowed


@pytest.mark.asyncio
async def test_evidence_failure_never_weakens_the_guard_denial(tmp_path: Path) -> None:
    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": []}

    async def broken_recorder(**_kwargs: object) -> bool:
        raise RuntimeError("evidence transport unavailable")

    verdict = await evaluate_mutation_request(
        MutationRequest("session", "tool", tmp_path, (Path("new.py"),)),
        provider="Provider",
        identity="seat/one",
        uri="ws://hub",
        token="token",
        timeout=0.1,
        state_fetcher=snapshot,
        git_runner=_runner(tmp_path),
        denial_recorder=broken_recorder,
    )

    assert not verdict.allowed
    assert verdict.reason_code == "GUARD_NO_CLAIM"
    assert "new.py" in verdict.reason
