# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse git-init` onboarding scaffold

from __future__ import annotations

from pathlib import Path

from synapse_channel.git.githook import HOOK_MARKER
from synapse_channel.git.gitinit import (
    SCAFFOLD_FILE,
    SCAFFOLD_MARKER,
    init_repo,
    repo_toplevel,
)


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
    lines = init_repo(
        uri="ws://h",
        name="ME",
        base_branch="trunk",
        hooks_dir=hooks,
        scaffold_dir=scaffold,
    )
    assert (hooks / "post-commit").exists()
    assert HOOK_MARKER in (hooks / "post-commit").read_text(encoding="utf-8")
    body = (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8")
    assert SCAFFOLD_MARKER in body
    assert "off `trunk`" in body
    assert "git-claim <task-id> --paths src/area --name ME" in body
    assert any("wrote .synapse/" in line for line in lines)


def test_init_repo_is_idempotent_and_updates_its_own_scaffold(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    scaffold = tmp_path / "scaffold"
    init_repo(uri="ws://h", name="ME", hooks_dir=hooks, scaffold_dir=scaffold)
    lines = init_repo(uri="ws://h", name="ME2", hooks_dir=hooks, scaffold_dir=scaffold)
    assert any("updated .synapse/" in line for line in lines)
    assert "--name ME2" in (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8")


def test_init_repo_skips_a_foreign_scaffold_file(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    (scaffold / SCAFFOLD_FILE).write_text("my own notes, keep them", encoding="utf-8")
    lines = init_repo(uri="ws://h", name="ME", hooks_dir=hooks, scaffold_dir=scaffold)
    assert any("skipped .synapse/" in line for line in lines)
    assert (scaffold / SCAFFOLD_FILE).read_text(encoding="utf-8") == "my own notes, keep them"


def test_init_repo_resolves_scaffold_dir_from_git_when_unset(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    top = tmp_path / "repo"
    top.mkdir()
    lines = init_repo(
        uri="ws://h",
        name="ME",
        runner=lambda args: str(top),  # repo_toplevel -> tmp_path/repo
        hooks_dir=hooks,  # so install_hooks never calls git
    )
    assert (top / ".synapse" / SCAFFOLD_FILE).exists()
    assert any("wrote .synapse/" in line for line in lines)
