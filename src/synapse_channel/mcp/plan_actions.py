# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP shared-plan handoff and task ledger actions
"""Translate MCP plan mutations into correlated hub operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType

Matcher = Callable[[dict[str, Any]], bool]
Sender = Callable[[], Awaitable[None]]
ReplyAwaiter = Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]


class McpPlanActions:
    """Own MCP handoff and shared-plan declare/update verbs.

    Parameters
    ----------
    agent : SynapseAgent
        Connected hub client used to issue plan operations.
    await_reply : ReplyAwaiter
        Correlator owned by the bridge transport layer.
    """

    def __init__(self, agent: SynapseAgent, await_reply: ReplyAwaiter) -> None:
        self.agent = agent
        self.await_reply = await_reply

    async def handoff(self, task_id: str, to_agent: str) -> str:
        """Hand a held task to another agent in one atomic step."""

        def match(data: dict[str, Any]) -> bool:
            return data.get("task_id") == task_id and data.get("type") in {
                MessageType.HANDOFF_GRANTED,
                MessageType.HANDOFF_DENIED,
            }

        reply = await self.await_reply(match, lambda: self.agent.handoff(task_id, to_agent))
        if reply is None:
            return f"handoff '{task_id}': no response from the hub"
        if reply.get("type") == MessageType.HANDOFF_GRANTED:
            return f"handed off '{task_id}' to {to_agent}"
        return f"handoff denied: '{task_id}' — {reply.get('payload') or 'rejected'}"

    async def task_declare(
        self, task_id: str, title: str, depends_on: list[str] | None = None
    ) -> str:
        """Declare (or refine) a task on the shared plan."""
        deps = tuple(depends_on or ())

        def match(data: dict[str, Any]) -> bool:
            return (
                data.get("type") == MessageType.LEDGER_TASK_POSTED
                and data.get("task", {}).get("task_id") == task_id
            )

        reply = await self.await_reply(
            match, lambda: self.agent.post_task(task_id, title=title, depends_on=deps)
        )
        if reply is None:
            return f"declare '{task_id}': no response from the hub"
        task = reply.get("task", {})
        return f"declared '{task_id}' — {task.get('title')}"

    async def task_update(
        self, task_id: str, status: str | None = None, suggested_owner: str | None = None
    ) -> str:
        """Update a plan task's status or suggested owner."""

        def match(data: dict[str, Any]) -> bool:
            return (
                data.get("type") == MessageType.LEDGER_TASK_UPDATED
                and data.get("task", {}).get("task_id") == task_id
            )

        reply = await self.await_reply(
            match,
            lambda: self.agent.update_ledger_task(
                task_id, status=status, suggested_owner=suggested_owner
            ),
        )
        if reply is None:
            return f"update '{task_id}': no response from the hub"
        task = reply.get("task", {})
        return f"updated '{task_id}' -> status={task.get('status')}"
