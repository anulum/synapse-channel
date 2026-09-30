# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real Git claim outcome journeys
"""Exercise dropped replies, bounded unknowns and read-only CLI recovery."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from cli_e2e_helpers import git_repo, run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.git.gitclaim import run_git_claim


async def test_lost_grant_is_confirmed_without_reclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lost broadcast still proves the exact live lease through a fresh query."""
    repo = git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(upstream)
        async with claim_proxy(proxy) as uri:
            result = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T",
                paths=["a.py"],
                auto_release_on="manual",
                reply_timeout=0.1,
            )
        assert result == 0
        assert "confirmed live claim" in capsys.readouterr().out
        assert proxy.requests.count("claim") == 1
        assert proxy.requests.count("state_request") == 1
        assert hub.state.claims["T"].epoch == 1


async def test_packaged_cli_recovers_and_releases_with_persisted_fence(tmp_path: Path) -> None:
    """Separate CLI processes recover without renewal and release the right epoch."""
    repo = git_repo(tmp_path / "repo")
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "SYN_PROJECT": "", "SYN_IDENTITY": ""}
    args = (
        "git-claim",
        "T",
        "--paths",
        "a.py",
        "--name",
        "me",
        "--auto-release-on",
        "manual",
        "--reply-timeout",
        "0.1",
    )
    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(upstream, drop_snapshots=True)
        async with claim_proxy(proxy) as uri:
            unknown = await asyncio.to_thread(run_cli, *args, uri=uri, cwd=repo, env=env)
        assert unknown.returncode == 3, unknown.output
        assert "outcome unknown" in unknown.output
        before = hub.state.claims["T"].as_dict()
        confirmation = ClaimProxy(upstream, drop_grants=False)
        async with claim_proxy(confirmation) as uri:
            confirmed = await asyncio.to_thread(
                run_cli,
                *args,
                "--confirm-only",
                uri=uri,
                cwd=repo,
                env=env,
            )
        assert confirmed.returncode == 0, confirmed.output
        assert confirmation.requests.count("claim") == 0
        assert hub.state.claims["T"].as_dict() == before
        released = await asyncio.to_thread(
            run_cli,
            "release",
            "T",
            "--name",
            "me",
            uri=upstream,
            cwd=repo,
            env=env,
        )
        assert released.returncode == 0, released.output
        assert "T" not in hub.state.claims


@pytest.mark.parametrize("raw", ["nan", "inf", "0", "-1", "301", "bad"])
def test_cli_rejects_invalid_deadlines_before_connecting(tmp_path: Path, raw: str) -> None:
    """Unbounded or malformed deadlines produce a usage refusal."""
    result = run_cli("git-claim", "T", "--reply-timeout", raw, cwd=tmp_path)
    assert result.returncode == 2
    assert "deadline must" in result.output


async def test_confirmation_of_absent_lease_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absence in current state is no evidence of a prior denial."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    async with running_hub() as (hub, uri):
        result = await run_git_claim(
            uri=uri,
            name="me",
            task_id="T",
            paths=["a.py"],
            confirm_only=True,
            auto_release_on="manual",
            reply_timeout=0.1,
        )
        assert not hub.state.claims
    assert result == 3
    assert "outcome unknown" in capsys.readouterr().out


async def test_default_deadline_accepts_grant_later_than_old_two_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual delayed positive reply survives the former two-second window."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(upstream, drop_grants=False, grant_delay=2.2)
        async with claim_proxy(proxy) as uri:
            result = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T",
                paths=["a.py"],
                auto_release_on="manual",
            )
        assert result == 0
        assert proxy.requests.count("state_request") == 0
        assert hub.state.claims["T"].epoch == 1


@pytest.mark.parametrize("deadline", [0.0, float("nan"), 301.0])
async def test_native_claim_rejects_invalid_deadline(deadline: float) -> None:
    """Invalid programmatic deadlines fail before Git or connection work."""
    assert (
        await run_git_claim(
            uri="ws://127.0.0.1:1",
            name="me",
            task_id="T",
            paths=[],
            reply_timeout=deadline,
        )
        == 2
    )


async def test_canonical_scope_failure_uses_fixed_refusal_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real symlink escaping the worktree never leaks resolver exception text."""
    repo = git_repo(tmp_path / "repo")
    outside = tmp_path / "private-outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    (repo / "outside.py").symlink_to(outside)
    monkeypatch.chdir(repo)
    result = await run_git_claim(
        uri="ws://127.0.0.1:1",
        name="me",
        task_id="T",
        paths=["outside.py"],
    )
    assert result == 1
    output = capsys.readouterr().out
    assert output == "claim path identity error: could not resolve canonical claim paths\n"
    assert str(outside) not in output
