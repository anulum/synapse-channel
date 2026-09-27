# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP project views over a real hub
"""Verify project projections against actual agent writes and hub snapshots."""

from __future__ import annotations

import json

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_views import ProjectMcpViews


async def post_task(
    handle: AgentHandle, task: str, project: str, *, dependencies: tuple[str, ...] = ()
) -> None:
    """Declare a real board task and await its published hub receipt."""
    await handle.agent.post_task(
        task,
        task + "_CONTENT",
        project=project,
        depends_on=dependencies,
        suggested_owner="BETA/worker",
    )
    await handle.recorder.wait_for(
        lambda frame: (
            frame.get("type") == "ledger_task_posted"
            and frame.get("task", {}).get("task_id") == task
        )
    )


async def advertise(handle: AgentHandle, marker: str) -> None:
    """Publish a live capability and resource through the actual agent surface."""
    await handle.agent.advertise(description=marker, skills=["python"])
    await handle.recorder.wait_for(
        lambda frame: (
            frame.get("type") == "capability_advertised" and frame.get("agent") == handle.agent.name
        )
    )
    await handle.agent.send_message("resource", kind="llm", name=marker, capacity=2)
    await handle.recorder.wait_for(
        lambda frame: (
            frame.get("type") == "resource_offered" and frame.get("agent") == handle.agent.name
        )
    )


async def test_project_views_hide_foreign_and_unscoped_hub_data() -> None:
    """All read tools and resource templates preserve project isolation."""
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA/worker", uri)
        beta = await connect_agent("BETA/worker", uri)
        bridge = await start_bridge(uri, name="ALPHA/remote")
        views = ProjectMcpViews(bridge.bridge, "ALPHA")
        try:
            await post_task(beta, "BETA_PRIVATE", "BETA")
            await post_task(alpha, "VISIBLE_DEP", "ALPHA")
            await alpha.agent.update_ledger_task("VISIBLE_DEP", suggested_owner="ALPHA/worker")
            await alpha.recorder.wait_for(
                lambda frame: (
                    frame.get("type") == "ledger_task_updated"
                    and frame.get("task", {}).get("task_id") == "VISIBLE_DEP"
                )
            )
            await post_task(alpha, "VISIBLE", "ALPHA", dependencies=("VISIBLE_DEP", "BETA_PRIVATE"))
            await post_task(alpha, "UNSCOPED_PRIVATE", "")
            await post_task(beta, "ALPHA/SPOOF_PRIVATE", "ALPHA")
            await alpha.agent.post_progress("VISIBLE", "VISIBLE_PROGRESS")
            await alpha.recorder.wait_for(
                lambda frame: frame.get("type") == "ledger_progress_posted"
            )
            await beta.agent.post_progress("VISIBLE", "FOREIGN_PROGRESS_PRIVATE")
            await beta.recorder.wait_for(
                lambda frame: frame.get("type") == "ledger_progress_posted"
            )
            await alpha.agent.claim("VISIBLE", paths=["alpha-only-scope"])
            await alpha.recorder.wait_for(
                lambda frame: (
                    frame.get("type") == "claim_granted" and frame.get("task_id") == "VISIBLE"
                )
            )
            await beta.agent.claim("BETA_PRIVATE", paths=["beta-only-scope"])
            await beta.recorder.wait_for(
                lambda frame: (
                    frame.get("type") == "claim_granted" and frame.get("task_id") == "BETA_PRIVATE"
                )
            )
            await advertise(alpha, "VISIBLE_RESOURCE")
            await advertise(beta, "BETA_RESOURCE_PRIVATE")

            board_text = await views.board()
            board = json.loads(board_text)
            assert {task["task_id"] for task in board["tasks"]} == {"VISIBLE", "VISIBLE_DEP"}
            task = next(item for item in board["tasks"] if item["task_id"] == "VISIBLE")
            assert task["depends_on"] == ["VISIBLE_DEP"]
            assert task["suggested_owner"] == ""
            own_task = next(item for item in board["tasks"] if item["task_id"] == "VISIBLE_DEP")
            assert own_task["suggested_owner"] == "ALPHA/worker"
            assert "VISIBLE" not in board["ready"]
            assert [note["text"] for note in board["progress"]] == ["VISIBLE_PROGRESS"]
            state_text = await views.state()
            state = json.loads(state_text)
            assert [claim["task_id"] for claim in state["active_claims"]] == ["VISIBLE"]
            assert {row["agent"] for row in state["agents"]} == {"ALPHA/worker", "ALPHA/remote"}
            assert "dead_letters" not in state and "pending_relay_approvals" not in state
            status = json.loads(await views.status())
            assert status["online_agents"] == 2
            assert status["active_claims"] == 1 and status["resources"] == 1
            assert status["identity"] == "ALPHA/remote"
            manifest = await views.manifest()
            assert [row["agent"] for row in json.loads(manifest)] == ["ALPHA/worker"]
            directory = await views.directory()
            task_resource = await views.task_resource("VISIBLE")
            assert json.loads(task_resource)["found"] is True
            foreign_resource = await views.task_resource("BETA_PRIVATE")
            assert json.loads(foreign_resource)["found"] is False
            own_resource = await views.agent_resource("ALPHA/worker")
            assert json.loads(own_resource)["found"] is True
            kind_resource = await views.resource_kind_resource("llm")
            assert len(json.loads(kind_resource)["resources"]) == 1
            with pytest.raises(PermissionError, match="remote MCP resource denied"):
                await views.agent_resource("BETA/worker")
            for rendered in (
                board_text,
                state_text,
                manifest,
                directory,
                task_resource,
                own_resource,
                kind_resource,
            ):
                assert "PRIVATE" not in rendered and "BETA/worker" not in rendered
        finally:
            await bridge.close()
            await close_agents(alpha, beta)


async def test_project_views_require_a_bound_project_identity() -> None:
    """Constructor cannot bind a foreign seat to a granted project."""
    async with running_hub() as (_, uri):
        bridge = await start_bridge(uri, name="BETA/remote")
        try:
            for project in ("", "ALPHA", "BETA/alias"):
                with pytest.raises(ValueError, match="outside its granted project"):
                    ProjectMcpViews(bridge.bridge, project)
        finally:
            await bridge.close()


async def test_project_views_refuse_queries_before_connection_ready() -> None:
    """A real bridge without a ready connection cannot return an empty success view."""
    async with running_hub() as (_, uri):
        bridge = SynapseHubBridge(uri=uri, name="ALPHA/not-ready", request_timeout=0.01)
        views = ProjectMcpViews(bridge, "ALPHA")
        with pytest.raises(RuntimeError, match="snapshot unavailable"):
            await views.board()
        with pytest.raises(RuntimeError, match="snapshot unavailable"):
            await views.manifest()
