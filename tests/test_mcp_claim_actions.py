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
