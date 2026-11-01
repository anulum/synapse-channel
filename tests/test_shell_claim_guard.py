# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral shell claim guard regressions

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claim_state import ClaimStateError
from synapse_channel.file_claim_guard import MutationRequest
from synapse_channel.shell_claim_guard import (
    ShellClaimGuardError,
    ShellRepository,
    ShellRequest,
    decide_shell_from_snapshot,
    evaluate_provider_request,
    evaluate_shell_request,
    resolve_shell_repository,
)


def _runner(root: Path, branch: str = "main") -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        assert args[-4:] == ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"]
        return f"{root}\n{branch}"

    return run


def _claim(
    root: Path,
    *,
    owner: str = "seat/one",
    paths: list[str] | None = None,
    status: str = "claimed",
    branch: str = "main",
) -> dict[str, Any]:
    return {
        "task_id": "SHELL",
        "owner": owner,
        "status": status,
        "worktree": str(root),
        "paths": [] if paths is None else paths,
        "git": {"branch": branch, "base": "main", "auto_release_on": "manual"},
    }


def test_resolve_shell_repository_accepts_subdirectory(tmp_path: Path) -> None:
    nested = tmp_path / "src"
    nested.mkdir()
    repository = resolve_shell_repository(
        ShellRequest("session", "tool", nested),
        provider="Provider",
        runner=_runner(tmp_path, "feature/shell"),
    )
    assert repository == ShellRepository(tmp_path.resolve(), "feature/shell")


def test_resolve_shell_repository_rejects_unsafe_cwd(tmp_path: Path) -> None:
    with pytest.raises(ShellClaimGuardError, match="must be absolute"):
        resolve_shell_repository(
            ShellRequest("session", "tool", Path("relative")),
            provider="Provider",
            runner=_runner(tmp_path),
        )
    missing = tmp_path / "missing"
    with pytest.raises(ShellClaimGuardError, match="readable directory"):
        resolve_shell_repository(
            ShellRequest("session", "tool", missing),
            provider="Provider",
            runner=_runner(tmp_path),
        )


def test_whole_worktree_claim_allows_shell(tmp_path: Path) -> None:
    verdict = decide_shell_from_snapshot(
        {"active_claims": [_claim(tmp_path)]},
        identity="seat/one",
        provider="Provider",
        repository=ShellRepository(tmp_path, "main"),
    )
    assert verdict.allowed


def test_shell_denies_competing_owner_on_another_branch(tmp_path: Path) -> None:
    verdict = decide_shell_from_snapshot(
        {
            "active_claims": [
                _claim(tmp_path),
                _claim(
                    tmp_path,
                    owner="seat/two",
                    paths=["src"],
                    branch="feature/other",
                ),
            ]
        },
        identity="seat/one",
        provider="Provider",
        repository=ShellRepository(tmp_path, "main"),
    )
    assert not verdict.allowed
    assert "ownership" in verdict.reason


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ([], "whole-worktree claim required"),
        ([{"paths": ["src"]}], "whole-worktree claim required"),
        ([{"owner": "seat/two"}], "ownership"),
        ([{"status": "done"}], "not editable"),
        ([{}, {"owner": "seat/two", "paths": ["src"]}], "ownership"),
    ],
)
def test_shell_denies_bounded_wrong_owner_stale_or_competing_claims(
    tmp_path: Path, claims: list[dict[str, object]], message: str
) -> None:
    active = []
    for override in claims:
        paths = override.get("paths")
        active.append(
            _claim(
                tmp_path,
                owner=str(override.get("owner", "seat/one")),
                paths=paths if isinstance(paths, list) else None,
                status=str(override.get("status", "claimed")),
            )
        )
    verdict = decide_shell_from_snapshot(
        {"active_claims": active},
        identity="seat/one",
        provider="Provider",
        repository=ShellRepository(tmp_path, "main"),
    )
    assert not verdict.allowed
    assert message in verdict.reason


def test_shell_rejects_malformed_relevant_claim(tmp_path: Path) -> None:
    malformed = _claim(tmp_path)
    malformed["paths"] = ""
    with pytest.raises(ShellClaimGuardError, match="malformed claim paths"):
        decide_shell_from_snapshot(
            {"active_claims": [malformed]},
            identity="seat/one",
            provider="Provider",
            repository=ShellRepository(tmp_path, "main"),
        )

    malformed = _claim(tmp_path)
    malformed["git"] = None
    with pytest.raises(ShellClaimGuardError, match="malformed claim Git context"):
        decide_shell_from_snapshot(
            {"active_claims": [malformed]},
            identity="seat/one",
            provider="Provider",
            repository=ShellRepository(tmp_path, "main"),
        )

    malformed = _claim(tmp_path)
    malformed["git"] = {"branch": 7}
    with pytest.raises(ShellClaimGuardError, match="malformed claim Git context"):
        decide_shell_from_snapshot(
            {"active_claims": [malformed]},
            identity="seat/one",
            provider="Provider",
            repository=ShellRepository(tmp_path, "main"),
        )


@pytest.mark.asyncio
async def test_evaluation_converts_state_failure_to_denial(tmp_path: Path) -> None:
    async def unavailable(**_kwargs: object) -> dict[str, Any]:
        raise ClaimStateError("hub unavailable")

    verdict = await evaluate_shell_request(
        ShellRequest("session", "tool", tmp_path),
        provider="Provider",
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=unavailable,
        git_runner=_runner(tmp_path),
    )
    assert not verdict.allowed
    assert verdict.reason == "hub unavailable"


def test_resolve_shell_repository_names_each_unusable_worktree(tmp_path: Path) -> None:
    """A NUL in the cwd, a non-Git directory, odd Git output and an escape are all refused."""
    request = ShellRequest("session", "tool", tmp_path)
    with pytest.raises(ShellClaimGuardError, match="not a valid path"):
        resolve_shell_repository(
            ShellRequest("session", "tool", tmp_path / "bad\0name"),
            provider="Provider",
            runner=_runner(tmp_path),
        )
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ShellClaimGuardError, match="not inside a readable Git worktree"):
        resolve_shell_repository(ShellRequest("session", "tool", plain), provider="Provider")
    with pytest.raises(ShellClaimGuardError, match="invalid worktree or branch context"):
        resolve_shell_repository(request, provider="Provider", runner=lambda _args: "only-root")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ShellClaimGuardError, match="escapes its Git worktree"):
        resolve_shell_repository(request, provider="Provider", runner=_runner(elsewhere))
    with pytest.raises(ShellClaimGuardError, match="escapes its Git worktree"):
        resolve_shell_repository(request, provider="Provider", runner=_runner(tmp_path / "gone"))


def _decide(snapshot: dict[str, Any], root: Path) -> bool:
    return decide_shell_from_snapshot(
        snapshot,
        identity="seat/one",
        provider="Provider",
        repository=ShellRepository(root, "main"),
    ).allowed


@pytest.mark.parametrize(
    ("snapshot_of", "message"),
    [
        (lambda _root: {"active_claims": "none"}, "no valid active_claims list"),
        (lambda _root: {"active_claims": ["claim"]}, "malformed claim"),
        (lambda _root: {"active_claims": [{"worktree": 7}]}, "invalid claim worktree"),
        (lambda _root: {"active_claims": [{"worktree": "/bad\0tree"}]}, "invalid claim worktree"),
        (lambda root: {"active_claims": [{**_claim(root), "owner": ""}]}, "malformed claim owner"),
        (lambda root: {"active_claims": [{**_claim(root), "status": 3}]}, "malformed claim status"),
    ],
)
def test_shell_refuses_a_malformed_hub_snapshot(
    tmp_path: Path, snapshot_of: Callable[[Path], dict[str, Any]], message: str
) -> None:
    """Every malformed part of the hub's claim snapshot fails closed with its reason."""
    with pytest.raises(ShellClaimGuardError, match=message):
        _decide(snapshot_of(tmp_path), tmp_path)


def test_shell_ignores_claims_without_a_worktree_and_refuses_an_invalid_own_root(
    tmp_path: Path,
) -> None:
    """Claims with no or blank worktree do not count; an invalid repository root is refused."""
    unscoped = [{**_claim(tmp_path), "worktree": None}, {**_claim(tmp_path), "worktree": "  "}]
    assert not _decide({"active_claims": unscoped}, tmp_path)
    assert _decide({"active_claims": [*unscoped, _claim(tmp_path)]}, tmp_path)
    other = tmp_path / "other-worktree"
    other.mkdir()
    foreign = _claim(other, owner="seat/two", status="stale")
    assert _decide({"active_claims": [foreign, _claim(tmp_path)]}, tmp_path)
    with pytest.raises(ShellClaimGuardError, match="Shell claim worktree is invalid"):
        _decide({"active_claims": [_claim(tmp_path)]}, tmp_path / "bad\0root")


async def test_provider_dispatch_sends_shell_and_file_calls_to_their_own_policy(
    tmp_path: Path,
) -> None:
    """In a real repository, one path claim admits the file edit but not a shell call."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.invalid"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "src" / "owned.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    root = repo.resolve()

    async def snapshot(**_kwargs: object) -> dict[str, Any]:
        return {"active_claims": [_claim(root, paths=["src/owned.py"])]}

    common: dict[str, Any] = {
        "provider": "Provider",
        "identity": "seat/one",
        "uri": "ws://hub",
        "token": None,
        "timeout": 0.1,
        "state_fetcher": snapshot,
    }
    shell = await evaluate_provider_request(ShellRequest("session", "tool", root), **common)
    assert not shell.allowed
    assert "whole-worktree claim required" in (shell.reason or "")
    edit = await evaluate_provider_request(
        MutationRequest("session", "tool", root, (Path("src/owned.py"),)), **common
    )
    assert edit.allowed, edit.reason
