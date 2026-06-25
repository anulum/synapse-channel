# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

from typing import Any

import pytest

from githook_helpers import _snapshot, make_factory
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.githook import (
    run_git_release,
)


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
