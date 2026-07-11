# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse git-init` onboarding scaffold

from __future__ import annotations

import os
from pathlib import Path

import pytest

from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.githook import HOOK_MARKER
from synapse_channel.git.gitinit import (
    SCAFFOLD_FILE,
    SCAFFOLD_MARKER,
    init_repo,
    repo_toplevel,
)


class _ConfigRunner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.values: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args == ["rev-parse", "--show-toplevel"]:
            return str(self.root)
        if args[:5] == ["config", "--local", "--get", "--default", ""]:
            return self.values.get(args[5], "")
        if args[:3] == ["config", "--local", "--unset-all"]:
            self.values.pop(args[3], None)
            return ""
        if args[:2] == ["config", "--local"] and len(args) == 4:
            self.values[args[2]] = args[3]
            return ""
        raise AssertionError(args)


def test_repo_toplevel_uses_rev_parse() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return "/work/repo"

    assert repo_toplevel(runner=runner) == Path("/work/repo")
    assert calls == [["rev-parse", "--show-toplevel"]]


def test_init_repo_installs_hooks_and_writes_scaffold(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    scaffold = tmp_path / "scaffold"
    runner = _ConfigRunner(tmp_path / "repo")
    lines = init_repo(
        uri="ws://h",
        name="ME",
        base_branch="trunk",
        hooks_dir=hooks,
        scaffold_dir=scaffold,
        runner=runner,
    )
    assert (hooks / "post-commit").exists()
    assert HOOK_MARKER in (hooks / "post-commit").read_text(encoding="utf-8")
    body = (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8")
    assert SCAFFOLD_MARKER in body
    assert "off `trunk`" in body
    assert "git-claim <task-id> --paths src/area --name ME" in body
    assert "synapse git-claim-check --staged" in body
    assert "only the post-commit and post-merge" in body
    assert runner.values["synapse.identity"] == "ME"
    assert runner.values["synapse.uri"] == "ws://h"
    assert any("wrote .synapse/" in line for line in lines)


def test_init_repo_is_idempotent_and_updates_its_own_scaffold(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    scaffold = tmp_path / "scaffold"
    runner = _ConfigRunner(tmp_path / "repo")
    init_repo(uri="ws://h", name="ME", hooks_dir=hooks, scaffold_dir=scaffold, runner=runner)
    lines = init_repo(
        uri="ws://h", name="ME2", hooks_dir=hooks, scaffold_dir=scaffold, runner=runner
    )
    assert any("updated .synapse/" in line for line in lines)
    assert "--name ME2" in (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8")


def test_init_repo_skips_a_foreign_scaffold_file(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    (scaffold / SCAFFOLD_FILE).write_text("my own notes, keep them", encoding="utf-8")
    lines = init_repo(
        uri="ws://h",
        name="ME",
        hooks_dir=hooks,
        scaffold_dir=scaffold,
        runner=_ConfigRunner(tmp_path / "repo"),
    )
    assert any("skipped .synapse/" in line for line in lines)
    assert (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8") == "my own notes, keep them"


def test_init_repo_resolves_scaffold_dir_from_git_when_unset(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    top = tmp_path / "repo"
    top.mkdir()
    lines = init_repo(
        uri="ws://h",
        name="ME",
        runner=_ConfigRunner(top),
        hooks_dir=hooks,  # so install_hooks never calls git
    )
    assert (top / ".synapse" / SCAFFOLD_FILE).exists()
    assert any("wrote .synapse/" in line for line in lines)


def test_init_repo_records_only_a_canonical_token_file_path(tmp_path: Path) -> None:
    runner = _ConfigRunner(tmp_path / "repo")
    token_file = tmp_path / "secrets" / "hub.token"
    lines = init_repo(
        uri="wss://hub",
        name="project/agent",
        token_file=str(token_file),
        hooks_dir=tmp_path / "hooks",
        scaffold_dir=tmp_path / "scaffold",
        runner=runner,
    )
    assert runner.values["synapse.tokenFile"] == str(token_file.resolve())
    assert (
        "token"
        not in " ".join(runner.values.values()).replace(str(token_file.resolve()), "").lower()
    )
    assert any("token content was not stored" in line for line in lines)


def test_init_repo_clears_a_stale_token_file_when_omitted(tmp_path: Path) -> None:
    runner = _ConfigRunner(tmp_path / "repo")
    runner.values["synapse.tokenFile"] = "/old/token"
    init_repo(
        uri="ws://hub",
        name="project/agent",
        hooks_dir=tmp_path / "hooks",
        scaffold_dir=tmp_path / "scaffold",
        runner=runner,
    )
    assert "synapse.tokenFile" not in runner.values
    assert ["config", "--local", "--unset-all", "synapse.tokenFile"] in runner.calls


def test_init_repo_reports_an_invalid_token_file_path(tmp_path: Path) -> None:
    token_loop = tmp_path / "token-loop"
    os.symlink(token_loop.name, token_loop)
    with pytest.raises(GitError, match="token-file path is invalid"):
        init_repo(
            uri="ws://hub",
            name="project/agent",
            token_file=str(token_loop),
            hooks_dir=tmp_path / "hooks",
            scaffold_dir=tmp_path / "scaffold",
            runner=_ConfigRunner(tmp_path / "repo"),
        )
