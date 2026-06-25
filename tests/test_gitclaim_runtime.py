# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import GitError, run_git_claim
from synapse_channel.git.githook import install_hooks


def _branch_then_repo(branch: str, repo: str) -> Callable[[list[str]], str]:
    """A git runner that answers branch and top-level queries distinctly."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo
        return branch

    return runner


def _branch_repo_hooks(branch: str, repo: str, hooks: str) -> Callable[[list[str]], str]:
    """A git runner answering branch, top-level, and hooks-dir queries distinctly."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo
        if args == ["rev-parse", "--git-path", "hooks"]:
            return hooks
        return branch

    return runner


async def test_run_git_claim_granted_sends_git_context() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T1",
            paths=["src/a.py"],
            base="develop",
            auto_release_on="commit",
            runner=_branch_then_repo("feature/x", "/repo"),
        )

    assert rc == 0
    claim = hub.state.claims["T1"]
    assert claim.owner == "me"
    assert claim.paths == ("src/a.py",)
    assert claim.worktree == "/repo"
    assert claim.git is not None
    assert claim.git.as_dict() == {
        "branch": "feature/x",
        "base": "develop",
        "auto_release_on": "commit",
    }


async def test_run_git_claim_uses_token() -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T1",
            paths=["src/a.py"],
            token=token,
            runner=_branch_then_repo("feature/x", "/repo"),
        )

    assert rc == 0
    assert hub.state.claims["T1"].owner == "me"


async def test_run_git_claim_warns_when_auto_release_hook_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="merge",
            runner=_branch_repo_hooks("feature/x", "/repo", str(tmp_path)),
        )

    assert rc == 0
    out = capsys.readouterr().out
    assert "will NOT fire" in out and "synapse git-hook" in out
    assert "synapse release T1 --name me" in out


async def test_run_git_claim_silent_when_hook_is_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_hooks(uri="ws://t", name="me", hooks_dir=tmp_path)
    async with running_hub(SynapseHub()) as (_hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="merge",
            runner=_branch_repo_hooks("feature/x", "/repo", str(tmp_path)),
        )

    assert rc == 0
    assert "will NOT fire" not in capsys.readouterr().out


async def test_run_git_claim_no_warning_for_manual_auto_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        rc = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="manual",
            runner=_branch_then_repo("feature/x", "/repo"),
        )

    assert rc == 0
    assert "will NOT fire" not in capsys.readouterr().out


async def test_run_git_claim_denied_by_existing_claim() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("BLOCK", worktree="/repo", paths=["src"])
            await alpha.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.CLAIM_GRANTED
                    and message.get("task_id") == "BLOCK"
                )
            )
            rc = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T1",
                paths=["src/a.py"],
                runner=_branch_then_repo("main", "/repo"),
                attempts=2,
            )
        finally:
            await close_agents(alpha)

    assert rc == 1


async def test_run_git_claim_unreachable_hub() -> None:
    rc = await run_git_claim(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="me",
        task_id="T1",
        paths=[],
        runner=_branch_then_repo("main", "/repo"),
        ready_timeout=0.1,
        attempts=1,
    )
    assert rc == 1


async def test_run_git_claim_git_error_on_repo_resolution() -> None:
    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            raise GitError("not inside a work tree")
        return "main"

    rc = await run_git_claim(
        uri="ws://127.0.0.1:1",
        name="me",
        task_id="T1",
        paths=[],
        runner=runner,
    )
    assert rc == 1


async def test_run_git_claim_git_error() -> None:
    def bad_runner(_args: list[str]) -> str:
        raise GitError("not a git repository")

    rc = await run_git_claim(
        uri="ws://127.0.0.1:1",
        name="me",
        task_id="T1",
        paths=[],
        runner=bad_runner,
    )
    assert rc == 1
