# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — query helpers for the reusable client
"""Read-side query helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

from typing import Any, Protocol

from synapse_channel.core.protocol import MessageType


class _QueryAgent(Protocol):
    """Envelope sender required by the read-side query mixin."""

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one message envelope to the hub."""


class AgentQueryMixin:
    """Send read-side request envelopes to the hub."""

    async def request_resume(self: _QueryAgent, since: int = 0) -> None:
        """Ask the hub for every chat message after a cursor.

        Use after a reconnect to catch up on exactly the messages missed.

        Parameters
        ----------
        since : int, optional
            The last chat ``msg_id`` already seen; the hub returns messages
            numbered above it. Defaults to ``0`` (the full history).
        """
        await self.send_message(
            MessageType.RESUME_REQUEST, target="System", payload="resume", since=int(since)
        )

    async def request_state(self: _QueryAgent) -> None:
        """Ask the hub for a full state snapshot."""
        await self.send_message(MessageType.STATE_REQUEST, target="System", payload="snapshot")

    async def request_who(self: _QueryAgent) -> None:
        """Ask the hub for the list of online agents."""
        await self.send_message(MessageType.WHO_REQUEST, target="System", payload="who")

    async def request_history(self: _QueryAgent, limit: int | None = 20) -> None:
        """Ask the hub for recent chat history.

        Parameters
        ----------
        limit : int or None, optional
            Number of recent messages to fetch (floored at ``1``), or ``None``
            for the full history. Defaults to ``20``.
        """
        if limit is None:
            await self.send_message(MessageType.HISTORY_REQUEST, target="System", payload="history")
            return
        n = max(1, int(limit))
        await self.send_message(
            MessageType.HISTORY_REQUEST, target="System", payload="history", limit=n
        )

    async def request_board(self: _QueryAgent) -> None:
        """Ask the hub for a snapshot of the shared blackboard."""
        await self.send_message(MessageType.BOARD_REQUEST, target="System", payload="board")

    async def request_manifest(self: _QueryAgent) -> None:
        """Ask the hub for the capability manifest of all advertised agents."""
        await self.send_message(MessageType.MANIFEST_REQUEST, target="System", payload="manifest")
