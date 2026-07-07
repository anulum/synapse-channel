# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for scoped hub claims

from __future__ import annotations

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.state import GitContext


async def test_claim_broadcasts_scope_and_epoch_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.claim("T1", worktree="wt", paths=["src"])
            granted = await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["worktree"] == "wt"
            assert granted["paths"] == ["src"]
            assert granted["epoch"] == 1
        finally:
            await close_agents(alpha)


async def test_claim_carries_git_context_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("A", uri)
        try:
            git = {"branch": "feature/x", "base": "main", "auto_release_on": "merge"}
            await alpha.agent.claim("T1", git=git)
            granted = await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["git"] == git
            assert hub.state.claims["T1"].git == GitContext(
                branch="feature/x", base="main", auto_release_on="merge"
            )
        finally:
            await close_agents(alpha)


async def test_claim_without_git_leaves_it_unset_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.claim("T1")
            granted = await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["git"] is None
            assert hub.state.claims["T1"].git is None
        finally:
            await close_agents(alpha)


async def test_scoped_claim_overlap_is_denied_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("A", uri)
        beta = await connect_agent("B", uri)
        try:
            await alpha.agent.claim("T1", paths=["src"])
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await beta.agent.claim("T2", paths=["src/app.py"])
            denied = await beta.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert "file scope conflicts with 'T1'" in denied["payload"]
        finally:
            await close_agents(alpha, beta)


async def test_traversal_like_claim_widens_to_whole_worktree_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("A", uri)
        beta = await connect_agent("B", uri)
        try:
            await alpha.agent.claim("T1", paths=["src/../tests"])
            granted = await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["paths"] == [""]
            assert hub.state.claims["T1"].paths == ("",)

            await beta.agent.claim("T2", paths=["docs"])
            denied = await beta.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert "file scope conflicts with 'T1'" in denied["payload"]
        finally:
            await close_agents(alpha, beta)


async def test_release_with_matching_epoch_is_granted_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            epoch = hub.state.claims["T1"].epoch
            await alpha.agent.release("T1", epoch=epoch)
            await alpha.recorder.wait_for(lambda m: m.get("type") == "release_granted")
        finally:
            await close_agents(alpha)


async def test_release_with_stale_epoch_is_denied_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.release("T1", epoch=999)
            denied = await alpha.recorder.wait_for(lambda m: m.get("type") == "release_denied")
            assert "epoch is stale" in denied["payload"]
            assert "T1" in hub.state.claims
        finally:
            await close_agents(alpha)


async def test_task_update_with_stale_epoch_errors_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("A", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.update_task("T1", status="done", epoch=999)
            error = await alpha.recorder.wait_for(lambda m: m.get("type") == "error")
            assert "epoch is stale" in error["payload"]
        finally:
            await close_agents(alpha)


def test_optional_int_parsing() -> None:
    assert SynapseHub._optional_int({"epoch": 5}, "epoch") == 5
    assert SynapseHub._optional_int({"epoch": 7.0}, "epoch") == 7
    assert SynapseHub._optional_int({"epoch": True}, "epoch") is None
    assert SynapseHub._optional_int({"epoch": "x"}, "epoch") is None
    assert SynapseHub._optional_int({}, "epoch") is None
    # A non-finite float (a JSON 1e400 decodes to inf) is treated as absent: int()
    # of it raises, which would otherwise escape the frame handler as a crash.
    assert SynapseHub._optional_int({"epoch": float("inf")}, "epoch") is None
    assert SynapseHub._optional_int({"epoch": float("-inf")}, "epoch") is None
    assert SynapseHub._optional_int({"epoch": float("nan")}, "epoch") is None
    # A large integer is finite and lossless (Python ints are arbitrary precision).
    assert SynapseHub._optional_int({"epoch": 10**400}, "epoch") == 10**400
