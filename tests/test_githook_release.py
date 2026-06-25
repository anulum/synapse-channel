# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.githook import (
    run_git_release,
)

GitPayload = dict[str, str]
ClaimSpec = tuple[str, list[str], GitPayload | None]


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("condition did not become true")


async def _claim_many(uri: str, owner: str, claims: list[ClaimSpec]) -> None:
    handle = await connect_agent(owner, uri)
    try:
        for task_id, paths, git in claims:
            await handle.agent.claim(task_id, paths=paths, git=git)

            def saw_claim_granted(message: dict[str, Any], expected: str = task_id) -> bool:
                return message.get("type") == "claim_granted" and message.get("task_id") == expected

            await handle.recorder.wait_for(saw_claim_granted)
    finally:
        await close_agents(handle)


async def test_run_git_release_releases_matching_claim(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [("T1", ["src/a.py"], {"branch": "x", "base": "main", "auto_release_on": "commit"})],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=lambda _args: "src/a.py\n",
        )

        assert rc == 0
        await _wait_until(lambda: "T1" not in hub.state.claims)
    assert "released on commit: T1" in capsys.readouterr().out


async def test_run_git_release_skips_non_matching_claims() -> None:
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "other",
            [
                (
                    "T1",
                    ["src/a.py"],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                )
            ],
        )
        await _claim_many(
            uri,
            "me",
            [
                ("T2", ["src/b.py"], None),
                ("T3", ["src/c.py"], {"branch": "x", "base": "main", "auto_release_on": "merge"}),
                ("T4", ["docs/x"], {"branch": "x", "base": "main", "auto_release_on": "commit"}),
                (
                    "T5",
                    ["src/target.py"],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                ),
            ],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=lambda _args: "src/target.py\n",
        )

        assert rc == 0
        await _wait_until(lambda: "T5" not in hub.state.claims)
        assert set(hub.state.claims) == {"T1", "T2", "T3", "T4"}


async def test_run_git_release_unreachable_hub_never_blocks() -> None:
    port = _free_port()
    rc = await run_git_release(
        uri=f"ws://localhost:{port}",
        name="me",
        trigger="commit",
        runner=lambda _args: "src/a.py\n",
        ready_timeout=0.1,
    )
    assert rc == 0


async def test_run_git_release_git_error_returns_one() -> None:
    def bad_runner(_args: list[str]) -> str:
        raise GitError("not a git repository")

    rc = await run_git_release(
        uri="ws://localhost:9", name="me", trigger="commit", runner=bad_runner, ready_timeout=0.1
    )
    assert rc == 1


async def test_run_git_release_without_active_claims() -> None:
    async with running_hub() as (hub, uri):
        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=lambda _args: "src/a.py\n",
        )

        assert rc == 0
        assert not hub.state.claims


async def test_run_git_release_releases_whole_worktree_claim() -> None:
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [("T1", [], {"branch": "x", "base": "main", "auto_release_on": "commit"})],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=lambda _args: "src/a.py\n",
        )

        assert rc == 0
        await _wait_until(lambda: "T1" not in hub.state.claims)
