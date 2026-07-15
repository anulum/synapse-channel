# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider-neutral shell claim guard regressions

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claim_state import ClaimStateError
from synapse_channel.shell_claim_guard import (
    ShellClaimGuardError,
    ShellRepository,
    ShellRequest,
    decide_shell_from_snapshot,
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
