# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub MCP claim action tests
"""Exercise the responsibility-split claim/release actions on a live hub."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run
from hub_e2e_helpers import running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.claim_actions import McpClaimActions


async def test_claim_actions_hold_scope_and_validate_release_receipt() -> None:
    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="mcp-claim-seat")
        try:
            assert isinstance(handle.bridge.claim_actions, McpClaimActions)
            claimed = await handle.bridge.claim("MCP-CLAIM-ACTIONS", ["src/owned.py"])
            recorded = hub.state.claims["MCP-CLAIM-ACTIONS"]
            released = await handle.bridge.release(
                "MCP-CLAIM-ACTIONS",
                evidence=["real-hub claim action test"],
                changed_files=["src/owned.py"],
                confidence="high",
            )
        finally:
            await handle.close()

    assert claimed == "claim granted: 'MCP-CLAIM-ACTIONS' (src/owned.py)"
    assert recorded.owner == "mcp-claim-seat"
    assert recorded.worktree
    assert recorded.paths == ("src/owned.py",)
    assert recorded.path_identity is not None
    assert released == "released 'MCP-CLAIM-ACTIONS' with receipt owner 'mcp-claim-seat'"
    assert "MCP-CLAIM-ACTIONS" not in hub.state.claims
    assert "evidence=real-hub claim action test" in hub.blackboard.progress[-1].text


async def test_git_claim_action_refuses_ambiguous_scope_before_hub_mutation() -> None:
    bridge = SynapseHubBridge(name="mcp-claim-seat", request_timeout=0.05)

    assert await bridge.git_claim("ESCAPE", ["../outside"]) == (
        "git claim refused: MCP Git claim paths must be bounded repository-relative "
        "paths without traversal."
    )


async def test_plain_mcp_claim_contends_with_git_dialect_but_not_linked_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain and Git MCP claims share one checkout identity, not all worktrees."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "src" / "owned.py"
    source.parent.mkdir()
    source.write_text("owned = True\n", encoding="utf-8")
    git_run(repo, "add", "src/owned.py")
    git_run(repo, "commit", "-q", "-m", "add owned source")
    linked = tmp_path / "linked"
    git_run(repo, "worktree", "add", "--detach", str(linked), "HEAD")

    async with running_hub() as (hub, uri):
        plain = await start_bridge(uri, name="plain-mcp")
        same_git = await start_bridge(uri, name="same-git-mcp")
        linked_git = await start_bridge(uri, name="linked-git-mcp")
        try:
            monkeypatch.chdir(repo)
            plain_result = await plain.bridge.claim("PLAIN", ["src/owned.py"])
            same_result = await same_git.bridge.git_claim("SAME-GIT", ["src/owned.py"])

            monkeypatch.chdir(linked)
            linked_result = await linked_git.bridge.git_claim("LINKED-GIT", ["src/owned.py"])
        finally:
            await plain.close()
            await same_git.close()
            await linked_git.close()

    assert "claim granted" in plain_result
    assert "claim denied" in same_result
    assert "file scope conflicts" in same_result
    assert "claim granted" in linked_result
    assert hub.state.claims["PLAIN"].worktree == repo.resolve().as_posix()
    assert hub.state.claims["LINKED-GIT"].worktree == linked.resolve().as_posix()


@pytest.mark.parametrize(
    ("receipt", "message"),
    [
        (None, "no valid receipt"),
        (
            {"task_id": "RECEIPT", "owner": "other", "released": True},
            "mismatched receipt",
        ),
    ],
)
async def test_release_action_refuses_missing_or_mismatched_receipts(
    receipt: dict[str, Any] | None, message: str
) -> None:
    bridge = SynapseHubBridge(name="mcp-claim-seat", request_timeout=0.5)
    release = asyncio.create_task(bridge.release("RECEIPT"))
    for _ in range(50):
        if bridge._waiters:
            break
        await asyncio.sleep(0)
    await bridge.on_message(
        {
            "type": MessageType.RELEASE_GRANTED,
            "task_id": "RECEIPT",
            "receipt": receipt,
        }
    )

    assert message in await release


def _repo_with_source(root: Path) -> Path:
    repo = git_repo(root)
    source = repo / "src" / "owned.py"
    source.parent.mkdir()
    source.write_text("owned = True\n", encoding="utf-8")
    git_run(repo, "add", "src/owned.py")
    git_run(repo, "commit", "-q", "-m", "add owned source")
    return repo


async def test_a_pathless_claim_inside_git_holds_and_contends_for_the_whole_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside a checkout a pathless claim is exactly the whole worktree the reply names."""
    repo = _repo_with_source(tmp_path / "repo")
    monkeypatch.chdir(repo)
    async with running_hub() as (hub, uri):
        whole = await start_bridge(uri, name="whole-seat")
        filer = await start_bridge(uri, name="file-seat")
        try:
            held = await whole.bridge.claim("WHOLE")
            blocked = await filer.bridge.claim("FILE", ["src/owned.py"])
            released = await whole.bridge.release("WHOLE")
            granted_after = await filer.bridge.claim("FILE", ["src/owned.py"])
            blocked_whole = await whole.bridge.claim("WHOLE-AGAIN")
        finally:
            await whole.close()
            await filer.close()
    root = repo.resolve().as_posix()
    assert held == f"claim granted: 'WHOLE' (the whole worktree {root})"
    assert "claim denied" in blocked and "file scope conflicts" in blocked
    assert "released 'WHOLE'" in released
    assert "claim granted" in granted_after
    assert "claim denied" in blocked_whole
    assert hub.state.claims["FILE"].worktree == root


async def test_a_pathless_claim_outside_git_is_refused_unless_task_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a checkout there is no worktree to claim, so only an explicit task lock goes."""
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.chdir(outside)
    async with running_hub() as (hub, uri):
        seat = await start_bridge(uri, name="loose-seat")
        try:
            refused = await seat.bridge.claim("LOOSE")
            mixed = await seat.bridge.claim("MIXED", ["a.py"], task_only=True)
            task_lock = await seat.bridge.claim("LOOSE", task_only=True)
        finally:
            await seat.close()
    assert refused == (
        "claim refused: outside a Git worktree a claim needs paths, or "
        "task_only=true for a task lock with no file scope"
    )
    assert mixed == "claim refused: task_only cannot be combined with paths"
    assert task_lock == "claim granted: 'LOOSE' (no file scope, task-only lock)"
    assert "MIXED" not in hub.state.claims
    assert (hub.state.claims["LOOSE"].worktree, hub.state.claims["LOOSE"].paths) == ("LOOSE", ())


async def test_a_task_only_lock_never_contends_with_file_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task lock has no file scope: it blocks only the same task id, like ``synapse lock``."""
    repo = _repo_with_source(tmp_path / "repo")
    monkeypatch.chdir(repo)
    async with running_hub() as (_hub, uri):
        locker = await start_bridge(uri, name="lock-seat")
        whole = await start_bridge(uri, name="whole-seat")
        try:
            locked = await locker.bridge.claim("DEPLOY", task_only=True)
            other_lock = await locker.bridge.claim("MIGRATE", task_only=True)
            whole_tree = await whole.bridge.claim("WHOLE")
            same_task = await whole.bridge.claim("DEPLOY", task_only=True)
        finally:
            await locker.close()
            await whole.close()
    assert "claim granted" in locked
    assert "claim granted" in other_lock
    assert "claim granted" in whole_tree
    assert "claim denied" in same_task


async def test_path_claims_keep_their_rules_inside_and_outside_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside Git a traversal path is refused; outside Git paths keep the legacy namespace."""
    repo = _repo_with_source(tmp_path / "repo")
    outside = tmp_path / "plain"
    outside.mkdir()
    async with running_hub() as (hub, uri):
        seat = await start_bridge(uri, name="path-seat")
        try:
            monkeypatch.chdir(repo)
            escaped = await seat.bridge.claim("ESCAPE", ["../outside"])
            monkeypatch.chdir(outside)
            legacy = await seat.bridge.claim("LEGACY", ["notes.txt"])
        finally:
            await seat.close()
    assert escaped.startswith("claim refused: ")
    assert "ESCAPE" not in hub.state.claims
    assert legacy == "claim granted: 'LEGACY' (notes.txt)"
    assert (hub.state.claims["LEGACY"].worktree, hub.state.claims["LEGACY"].paths) == (
        "",
        ("notes.txt",),
    )
