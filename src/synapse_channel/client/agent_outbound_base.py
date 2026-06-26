# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — base outbound send helpers
"""Base outbound send helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

import json
from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.protocol import MessageType, build_envelope

__all__ = ["AgentSendMixin"]


class AgentSendMixin:
    """Send raw envelopes and chat messages."""

    async def send_message(
        self: _OutboundAgent,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Serialise and send one message envelope to the hub.

        Parameters
        ----------
        msg_type : str
            One of the :class:`~synapse_channel.core.protocol.MessageType` constants.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        payload : str, optional
            Free-form text body.
        **extra : Any
            Additional protocol fields merged into the envelope.
        """
        if self.connection is None:
            return
        msg = build_envelope(self.name, msg_type, target=target, payload=payload, **extra)
        await self.connection.send(json.dumps(msg))

    async def chat(
        self: _OutboundAgent,
        payload: str,
        *,
        target: str = "all",
        priority: bool = False,
        memory_tag: str = "",
    ) -> None:
        """Send a chat message to the room or a single agent.

        Parameters
        ----------
        payload : str
            Message text.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        priority : bool, optional
            Mark the message as priority so it wakes directed-only waiters.
        memory_tag : str, optional
            Opaque tag marking the message memory-worthy.
        """
        extra: dict[str, Any] = {}
        if priority:
            extra["priority"] = True
        if memory_tag:
            extra["memory_tag"] = memory_tag
        await self.send_message(MessageType.CHAT, target=target, payload=payload, **extra)
