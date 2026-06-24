# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.git.gitclaim import AgentFactory, GitError
from synapse_channel.git.githook import (
    HOOK_MARKER,
    _paths_overlap,
    changed_files,
    hook_installed,
    hooks_directory,
    install_hooks,
    run_git_release,
)


class FakeAgent:
    """A SynapseAgent stand-in that replays an inbound snapshot and records releases."""

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
        self.releases: list[str] = []
        self.state_requests = 0

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_state(self) -> None:
        self.state_requests += 1

    async def release(self, task_id: str, **_kw: Any) -> None:
        self.releases.append(task_id)


def make_factory(
    *, ready: bool = True, inbound: list[dict[str, Any]] | None = None
) -> tuple[AgentFactory, list[FakeAgent]]:
    created: list[FakeAgent] = []

    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, ready=ready, inbound=inbound, **kwargs)
        created.append(agent)
        return agent

    return cast(AgentFactory, factory), created


def _snapshot(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "state_snapshot", "snapshot": {"active_claims": claims}}


# -- hooks_directory + install_hooks ------------------------------------------


def test_hooks_directory_uses_rev_parse() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "/repo/.git/hooks"

    assert hooks_directory(runner=runner) == Path("/repo/.git/hooks")
    assert captured == [["rev-parse", "--git-path", "hooks"]]


def test_install_hooks_writes_executable_hooks(tmp_path: Path) -> None:
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("post-commit" in line for line in lines)
    assert any("post-merge" in line for line in lines)
    for filename, trigger in [("post-commit", "commit"), ("post-merge", "merge")]:
        hook = tmp_path / filename
        body = hook.read_text(encoding="utf-8")
        assert HOOK_MARKER in body
        assert f"git-release --trigger {trigger}" in body
        assert "--name ME" in body
        assert os.access(hook, os.X_OK)
        assert hook.stat().st_mode & stat.S_IXUSR


def test_install_hooks_bakes_token_file(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", token_file="/etc/synapse.token", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "--token-file /etc/synapse.token" in body


def test_install_hooks_bakes_an_explicit_synapse_bin(tmp_path: Path) -> None:
    install_hooks(
        uri="ws://h", name="ME", synapse_bin="/opt/synapse/bin/synapse", hooks_dir=tmp_path
    )
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "/opt/synapse/bin/synapse git-release --trigger commit" in body


def test_install_hooks_resolves_an_absolute_synapse_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "synapse_channel.git.githook.shutil.which", lambda _name: "/usr/local/bin/synapse"
    )
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "/usr/local/bin/synapse git-release" in body


def test_install_hooks_falls_back_to_bare_name_when_synapse_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("synapse_channel.git.githook.shutil.which", lambda _name: None)
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "\nsynapse git-release" in body  # bare name resolved from PATH at hook time


def test_install_hooks_overwrites_its_own_hook(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    lines = install_hooks(uri="ws://h", name="ME2", hooks_dir=tmp_path)
    assert all("installed" in line for line in lines)
    assert "--name ME2" in (tmp_path / "post-commit").read_text(encoding="utf-8")


def test_install_hooks_skips_foreign_hook(tmp_path: Path) -> None:
    foreign = tmp_path / "post-commit"
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("skipped post-commit" in line for line in lines)
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"  # untouched
    assert (tmp_path / "post-merge").exists()  # the non-conflicting one is still installed


def test_install_hooks_resolves_dir_from_runner(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", runner=lambda _a: str(tmp_path))
    assert (tmp_path / "post-commit").exists()


def test_hook_installed_true_after_install(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("merge", hooks_dir=tmp_path) is True
    assert hook_installed("commit", hooks_dir=tmp_path) is True


def test_hook_installed_false_when_absent(tmp_path: Path) -> None:
    assert hook_installed("merge", hooks_dir=tmp_path) is False


def test_hook_installed_false_for_a_foreign_hook(tmp_path: Path) -> None:
    (tmp_path / "post-merge").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert hook_installed("merge", hooks_dir=tmp_path) is False  # no marker → not ours


def test_hook_installed_unknown_trigger_is_false(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("push", hooks_dir=tmp_path) is False


def test_hook_installed_resolves_dir_from_runner(tmp_path: Path) -> None:
    install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert hook_installed("merge", runner=lambda _a: str(tmp_path)) is True


def test_install_hooks_shell_quotes_values(tmp_path: Path) -> None:
    # A name with shell metacharacters must be quoted, not injected into the hook.
    install_hooks(uri="ws://h", name="x; echo PWNED #", hooks_dir=tmp_path)
    body = (tmp_path / "post-commit").read_text(encoding="utf-8")
    assert "'x; echo PWNED #'" in body
    assert "--name x; echo" not in body


def test_install_hooks_skips_binary_foreign_hook(tmp_path: Path) -> None:
    # A non-UTF-8 hook from something else must be detected and left untouched, not crash.
    (tmp_path / "post-commit").write_bytes(b"\xff\xfe\x00binary")
    lines = install_hooks(uri="ws://h", name="ME", hooks_dir=tmp_path)
    assert any("skipped post-commit" in line for line in lines)
    assert (tmp_path / "post-commit").read_bytes() == b"\xff\xfe\x00binary"


# -- changed_files ------------------------------------------------------------


def test_changed_files_commit_uses_diff_tree() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "src/a.py\nsrc/b.py\n"

    assert changed_files("commit", runner=runner) == ["src/a.py", "src/b.py"]
    assert captured == [["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"]]


def test_changed_files_merge_uses_orig_head() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "x\n"

    changed_files("merge", runner=runner)
    assert captured == [["diff", "--name-only", "ORIG_HEAD", "HEAD"]]


def test_changed_files_drops_blank_lines() -> None:
    assert changed_files("commit", runner=lambda _a: "a\n\n   \nb\n") == ["a", "b"]


# -- _paths_overlap -----------------------------------------------------------


def test_paths_overlap_whole_worktree() -> None:
    assert _paths_overlap([], ["any.py"]) is True
    assert _paths_overlap([], []) is False


def test_paths_overlap_exact_prefix_and_miss() -> None:
    assert _paths_overlap(["src/a.py"], ["src/a.py"]) is True
    assert _paths_overlap(["src"], ["src/a.py"]) is True
    assert _paths_overlap(["src/"], ["src/a.py"]) is True
    assert _paths_overlap(["src/a.py"], ["src/b.py"]) is False
    assert _paths_overlap(["docs"], ["src/a.py"]) is False


# -- run_git_release ----------------------------------------------------------


async def test_run_git_release_releases_matching_claim() -> None:
    claims = [
        {
            "task_id": "T1",
            "owner": "me",
            "paths": ["src/a.py"],
            "git": {"branch": "x", "base": "main", "auto_release_on": "commit"},
        }
    ]
    factory, created = make_factory(
        inbound=[{"type": "chat", "payload": "noise"}, _snapshot(claims)]
    )
    rc = await run_git_release(
        uri="ws://t",
        name="me",
        trigger="commit",
        agent_factory=factory,
        runner=lambda _a: "src/a.py\n",
    )
    assert rc == 0
    assert created[0].releases == ["T1"]


async def test_run_git_release_skips_non_matching_claims() -> None:
    claims: list[dict[str, Any]] = [
        {
            "task_id": "T1",
            "owner": "other",
            "paths": ["src/a.py"],
            "git": {"auto_release_on": "commit"},
        },
        {"task_id": "T2", "owner": "me", "paths": ["src/a.py"], "git": None},
        {
            "task_id": "T3",
            "owner": "me",
            "paths": ["src/a.py"],
            "git": {"auto_release_on": "merge"},
        },
        {"task_id": "T4", "owner": "me", "paths": ["docs/x"], "git": {"auto_release_on": "commit"}},
        {
            "task_id": "T5",
            "owner": "me",
            "paths": ["src/a.py"],
            "git": {"auto_release_on": "commit"},
        },
    ]
    factory, created = make_factory(inbound=[_snapshot(claims)])
    rc = await run_git_release(
        uri="ws://t",
        name="me",
        trigger="commit",
        agent_factory=factory,
        runner=lambda _a: "src/a.py\n",
    )
    assert rc == 0
    assert created[0].releases == ["T5"]


async def test_run_git_release_unreachable_hub_never_blocks() -> None:
    factory, created = make_factory(ready=False)
    rc = await run_git_release(
        uri="ws://t",
        name="me",
        trigger="commit",
        agent_factory=factory,
        runner=lambda _a: "src/a.py\n",
    )
    assert rc == 0
    assert created[0].releases == []


async def test_run_git_release_git_error_returns_one() -> None:
    def bad_runner(_args: list[str]) -> str:
        raise GitError("not a git repository")

    factory, _created = make_factory()
    rc = await run_git_release(
        uri="ws://t", name="me", trigger="commit", agent_factory=factory, runner=bad_runner
    )
    assert rc == 1


async def test_run_git_release_without_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.git.githook.asyncio.sleep", no_sleep)
    factory, created = make_factory(inbound=[])
    rc = await run_git_release(
        uri="ws://t",
        name="me",
        trigger="commit",
        agent_factory=factory,
        runner=lambda _a: "src/a.py\n",
    )
    assert rc == 0
    assert created[0].releases == []


async def test_run_git_release_tolerates_none_paths() -> None:
    # A claim with an explicit None scope must be treated as the whole worktree, not crash.
    claims: list[dict[str, Any]] = [
        {"task_id": "T1", "owner": "me", "paths": None, "git": {"auto_release_on": "commit"}}
    ]
    factory, created = make_factory(inbound=[_snapshot(claims)])
    rc = await run_git_release(
        uri="ws://t",
        name="me",
        trigger="commit",
        agent_factory=factory,
        runner=lambda _a: "src/a.py\n",
    )
    assert rc == 0
    assert created[0].releases == ["T1"]
