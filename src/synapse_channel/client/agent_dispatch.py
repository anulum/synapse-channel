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
    mailbox: bool
    mailbox_advance: Callable[[dict[str, Any]], bool] | None
    name: str
    ready_event: Any
    verbose: bool
    _mailbox_since_seq: int

    async def ack(self, seq: int) -> bool:
        """Acknowledge a delivered directed message by its durable seq."""

    async def _track_mailbox_frame(self, data: dict[str, Any]) -> None:
        """Advance the mailbox cursor on a chat frame and ack a replayed one."""


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

        if self.mailbox and data.get("type") == MessageType.CHAT:
            await self._track_mailbox_frame(data)

        # Ignore our own chat echoes, but still process system replies.
        if data.get("sender") == self.name and data.get("type") == MessageType.CHAT:
            return
        if self.callback is not None:
            await self.callback(data)

    async def _track_mailbox_frame(self: _DispatchAgent, data: dict[str, Any]) -> None:
        """Advance the mailbox cursor on a chat frame and ack a replayed one.

        Every chat frame carries its durable journal ``seq``; the cursor tracks the
        highest one seen so a reconnect resumes its backlog from there. A frame the
        hub marked ``replayed`` is one this agent missed while offline, so it is
        acknowledged by that ``seq`` — a no-op at the hub unless a sender is awaiting
        a deferred delivery receipt, in which case the ack releases it. A missing or
        non-integer ``seq`` is ignored rather than allowed to reset the cursor.

        When the agent carries a ``mailbox_advance`` gate, a frame the gate refuses
        neither advances the cursor nor acks — the frame stays pending for a later
        replay instead of being silently consumed by a receiver that will never
        surface it (the message-loss guard behind the 2026-07-10 P0).
        """
        seq = data.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            return
        if self.mailbox_advance is not None and not self.mailbox_advance(data):
            return
        if seq > self._mailbox_since_seq:
            self._mailbox_since_seq = seq
        if data.get("replayed") is True:
            await self.ack(seq)
