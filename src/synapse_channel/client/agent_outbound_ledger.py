# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound ledger helpers
"""Outbound shared-ledger helpers for the reusable client."""

from __future__ import annotations

from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.protocol import MessageType

__all__ = ["AgentLedgerMixin"]


class AgentLedgerMixin:
    """Send task-board and progress-ledger envelopes."""

    async def post_task(
        self: _OutboundAgent,
        task_id: str,
        title: str,
        *,
        description: str = "",
        depends_on: tuple[str, ...] | list[str] = (),
        suggested_owner: str = "",
    ) -> None:
        """Declare or re-declare a task on the shared plan."""
        extra: dict[str, Any] = {"task_id": task_id.strip(), "title": title}
        if description:
            extra["description"] = description
        if depends_on:
            extra["depends_on"] = list(depends_on)
        if suggested_owner:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK, target="System", **extra)

    async def update_ledger_task(
        self: _OutboundAgent,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
    ) -> None:
        """Change a plan task's planning status or suggested owner."""
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if suggested_owner is not None:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK_UPDATE, target="System", **extra)

    async def post_progress(
        self: _OutboundAgent, task_id: str, text: str, *, kind: str = "note"
    ) -> None:
        """Append a structured progress note to the progress ledger."""
        await self.send_message(
            MessageType.LEDGER_PROGRESS,
            target="System",
            payload=text,
            task_id=task_id.strip(),
            kind=kind,
        )
