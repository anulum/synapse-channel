# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — inbound dispatch helpers for the reusable client
"""Inbound dispatch helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from synapse_channel.core.protocol import MessageType, read_protocol_version

MessageCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Async callback invoked with each decoded inbound message."""


class _DispatchAgent(Protocol):
    """Attributes required by the inbound dispatch mixin."""

    callback: MessageCallback | None
    hub_id: str
    hub_protocol_version: int | None
    name: str
    ready_event: Any
    verbose: bool


class AgentDispatchMixin:
    """Decode inbound WebSocket frames and forward callback-visible messages."""

    async def _dispatch(self: _DispatchAgent, raw: str | bytes) -> None:
        """Decode one raw frame and forward it to the callback.

        Parameters
        ----------
        raw : str or bytes
            The raw WebSocket frame received from the hub.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            if self.verbose:
                print(f"[{self.name}] Received malformed JSON from hub.")
            return

        if data.get("type") == MessageType.WELCOME:
            self.hub_id = str(data.get("hub_id", "unknown"))
            self.hub_protocol_version = read_protocol_version(data.get("protocol_version"))
            self.ready_event.set()

        # Ignore our own chat echoes, but still process system replies.
        if data.get("sender") == self.name and data.get("type") == MessageType.CHAT:
            return
        if self.callback is not None:
            await self.callback(data)
