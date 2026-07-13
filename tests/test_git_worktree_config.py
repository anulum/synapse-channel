# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-worktree staged-claim configuration tests

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from synapse_channel.git.claim_check_config import (
    persist_claim_check_config,
    read_claim_check_config,
)
from synapse_channel.git.claim_check_context import resolve_claim_check_context
from synapse_channel.git.gitclaim import GitError, GitRunner


class _ConfigRunner:
    """Small two-scope Git-config model for fail-closed edge cases."""

    def __init__(
        self,
        *,
        common: dict[str, str] | None = None,
        worktree: dict[str, str] | None = None,
    ) -> None:
        self.common = common or {}
        self.worktree = worktree or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:6] == ["config", "--local", "--type=bool", "--get", "--default", "false"]:
            return (
                "true"
                if self.common.get(args[6], "").lower() in {"1", "on", "true", "yes"}
                else "false"
            )
        if args[:5] == ["config", "--local", "--get", "--default", ""]:
            return self.common.get(args[5], "")
        if args[:5] == ["config", "--worktree", "--get", "--default", ""]:
            return self.worktree.get(args[5], "")
        if args[:3] == ["config", "--local", "--unset-all"]:
            self.common.pop(args[3], None)
            return ""
        if args[:3] == ["config", "--worktree", "--unset-all"]:
            self.worktree.pop(args[3], None)
            return ""
        if args[:2] == ["config", "--local"] and len(args) == 4:
            self.common[args[2]] = args[3]
            return ""
        if args[:2] == ["config", "--worktree"] and len(args) == 4:
            self.worktree[args[2]] = args[3]
            return ""
        raise AssertionError(args)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603, S607 - fixed test-only Git invocation
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _runner(repo: Path) -> GitRunner:
    return lambda args: _git(repo, *args)


def _linked_repo(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.name", "worktree-test")
    _git(main, "config", "user.email", "worktree@example.test")
    (main / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(main, "add", "tracked.txt")
    _git(main, "commit", "-q", "-m", "base")
    _git(main, "worktree", "add", "-q", "-b", "seat-b", str(linked))
    return main, linked


def test_two_real_linked_worktrees_keep_distinct_claim_identities(tmp_path: Path) -> None:
    main, linked = _linked_repo(tmp_path)
    persist_claim_check_config(
        uri="ws://main", name="project/seat-a", token_file=None, runner=_runner(main)
    )
    persist_claim_check_config(
        uri="ws://linked", name="project/seat-b", token_file=None, runner=_runner(linked)
    )

    main_context = resolve_claim_check_context(runner=_runner(main), environment={})
    linked_context = resolve_claim_check_context(runner=_runner(linked), environment={})
    assert (main_context.identity, main_context.uri, main_context.branch) == (
        "project/seat-a",
        "ws://main",
        "main",
    )
    assert (linked_context.identity, linked_context.uri, linked_context.branch) == (
        "project/seat-b",
        "ws://linked",
        "seat-b",
    )
    assert _git(main, "config", "--local", "--get", "--default", "", "synapse.identity") == ""
    assert _git(main, "config", "--worktree", "--get", "synapse.identity") == "project/seat-a"
    assert _git(linked, "config", "--worktree", "--get", "synapse.identity") == "project/seat-b"


def test_persist_migrates_shared_values_and_clears_only_the_current_token(tmp_path: Path) -> None:
    token_file = tmp_path / "hub.token"
    runner = _ConfigRunner(
        common={
            "synapse.identity": "legacy/seat",
            "synapse.uri": "ws://legacy",
            "synapse.tokenFile": "/legacy/token",
        },
        worktree={"synapse.tokenFile": "/current/token"},
    )
    lines, canonical = persist_claim_check_config(
        uri="wss://new",
        name="project/seat",
        token_file=str(token_file),
        runner=runner,
    )
    assert canonical == str(token_file.resolve())
    assert runner.common == {"extensions.worktreeConfig": "true"}
    assert runner.worktree == {
        "synapse.identity": "project/seat",
        "synapse.uri": "wss://new",
        "synapse.tokenFile": str(token_file.resolve()),
    }
    assert any("token content was not stored" in line for line in lines)

    lines, canonical = persist_claim_check_config(
        uri="wss://new", name="project/seat", token_file=None, runner=runner
    )
    assert canonical is None
    assert "synapse.tokenFile" not in runner.worktree
    assert any("no token file requested" in line for line in lines)


@pytest.mark.parametrize(
    "unsafe",
    [
        {"core.worktree": "/other/tree"},
        {"core.bare": "yes"},
    ],
)
def test_persist_refuses_common_git_values_that_require_manual_migration(
    unsafe: dict[str, str],
) -> None:
    runner = _ConfigRunner(common=unsafe)
    with pytest.raises(GitError, match="cannot safely enable per-worktree"):
        persist_claim_check_config(
            uri="ws://hub", name="project/seat", token_file=None, runner=runner
        )
    assert "extensions.worktreeConfig" not in runner.common


def test_enabled_worktree_reads_never_fall_back_to_shared_values() -> None:
    runner = _ConfigRunner(
        common={"extensions.worktreeConfig": "on", "synapse.identity": "shared/seat"}
    )
    assert read_claim_check_config("synapse.identity", runner=runner) == ""
    runner.worktree["synapse.identity"] = "worktree/seat"
    assert read_claim_check_config("synapse.identity", runner=runner) == "worktree/seat"


def test_legacy_read_and_invalid_token_failure(tmp_path: Path) -> None:
    runner = _ConfigRunner(common={"synapse.identity": "legacy/seat"})
    assert read_claim_check_config("synapse.identity", runner=runner) == "legacy/seat"

    token_loop = tmp_path / "token-loop"
    os.symlink(token_loop.name, token_loop)
    with pytest.raises(GitError, match="token-file path is invalid"):
        persist_claim_check_config(
            uri="ws://hub",
            name="project/seat",
            token_file=str(token_loop),
            runner=runner,
        )
    assert runner.common == {"synapse.identity": "legacy/seat"}
    assert runner.worktree == {}
