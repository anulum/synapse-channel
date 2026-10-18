# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP mutation tool result envelopes
"""Expose scoped outcomes in structured content while retaining legacy text."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.outcome import McpMutation, McpOutcomeFailure, OutcomeText


async def invoke(action: Awaitable[str]) -> CallToolResult:
    """Return native MCP text and independent machine-readable outcome truth."""
    is_error = False
    try:
        text = await action
        outcome = (
            text.outcome
            if isinstance(text, OutcomeText)
            else McpMutation().unknown("unqualified legacy result").outcome
        )
    except McpOutcomeFailure as exc:
        text = str(exc)
        outcome = exc.outcome
        is_error = True
    return CallToolResult(
        content=[TextContent(type="text", text=str(text))],
        structuredContent={"result": str(text), "execution_outcome": outcome.to_json()},
        isError=is_error,
    )


def register_mutation_tools(server: FastMCP, bridge: SynapseHubBridge) -> None:
    """Register mutation adapters with explicit scoped execution observations."""
    _register_lease_tools(server, bridge)
    _register_plan_tools(server, bridge)
    _register_app_tools(server, bridge)


def _register_lease_tools(server: FastMCP, bridge: SynapseHubBridge) -> None:
    """Register lease acquisition, exact confirmation and receipt-bearing release."""

    @server.tool(structured_output=False)
    async def synapse_claim(
        task_id: str, paths: list[str] | None = None, task_only: bool = False
    ) -> CallToolResult:
        """Claim a task lease on file paths, the whole current Git worktree, or the task alone.

        Without paths the claim covers the MCP process's whole Git worktree, and is
        refused outside one; ``task_only`` leases the task id alone with no file scope.
        """
        return await invoke(bridge.claim(task_id, paths, task_only=task_only))

    @server.tool(structured_output=False)
    async def synapse_git_claim(
        task_id: str,
        paths: list[str] | None = None,
        base: str = "main",
        auto_release_on: str = "manual",
        whole_worktree: bool = False,
        reply_timeout: float = 30.0,
        confirm_only: bool = False,
    ) -> CallToolResult:
        """Claim or read-only confirm exact Git paths; unknown never authorises edits."""
        return await invoke(
            bridge.git_claim(
                task_id,
                paths,
                base=base,
                auto_release_on=auto_release_on,
                whole_worktree=whole_worktree,
                reply_timeout=reply_timeout,
                confirm_only=confirm_only,
            )
        )

    @server.tool(structured_output=False)
    async def synapse_release(
        task_id: str,
        evidence: list[str] | None = None,
        changed_files: list[str] | None = None,
        confidence: str = "",
    ) -> CallToolResult:
        """Release a task lease with optional durable receipt evidence."""
        return await invoke(
            bridge.release(
                task_id,
                evidence=evidence or (),
                changed_files=changed_files or (),
                confidence=confidence,
            )
        )


def _register_plan_tools(server: FastMCP, bridge: SynapseHubBridge) -> None:
    """Register message submission, handoff and shared-plan mutations."""

    @server.tool(structured_output=False)
    async def synapse_send(target: str, message: str) -> CallToolResult:
        """Send a chat message to an agent, a group, or everyone (``all``)."""
        return await invoke(bridge.send(target, message))

    @server.tool(structured_output=False)
    async def synapse_handoff(task_id: str, to_agent: str) -> CallToolResult:
        """Hand a held task to another online agent atomically."""
        return await invoke(bridge.handoff(task_id, to_agent))

    @server.tool(structured_output=False)
    async def synapse_task_declare(
        task_id: str, title: str, depends_on: list[str] | None = None
    ) -> CallToolResult:
        """Declare or refine a task on the shared plan."""
        return await invoke(bridge.task_declare(task_id, title, depends_on))

    @server.tool(structured_output=False)
    async def synapse_task_update(
        task_id: str, status: str | None = None, suggested_owner: str | None = None
    ) -> CallToolResult:
        """Update a plan task's status or suggested owner."""
        return await invoke(bridge.task_update(task_id, status, suggested_owner))


def _register_app_tools(server: FastMCP, bridge: SynapseHubBridge) -> None:
    """Register local app task mutations without a business-success claim."""

    @server.tool(structured_output=False)
    async def synapse_app_task_offer(bundle: dict[str, Any]) -> CallToolResult:
        """Offer an explicit human app task against a current local allowance."""
        return await invoke(bridge.app_task_offer(bundle))

    @server.tool(structured_output=False)
    async def synapse_app_task_attach(task_id: str, result: dict[str, Any]) -> CallToolResult:
        """Attach an untrusted result envelope to its offered task."""
        return await invoke(bridge.app_task_attach(task_id, result))
