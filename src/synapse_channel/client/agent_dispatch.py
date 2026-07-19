# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — inbound dispatch helpers for the reusable client
"""Inbound dispatch helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from synapse_channel.core.protocol import MessageType, read_protocol_version

MessageCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Preferred inbound callback: ``async def callback(data) -> None``.

Runtime compatibility (K3-B1): a synchronous callable that returns ``None`` is
also accepted by :func:`invoke_message_callback`, which awaits only when
:func:`inspect.isawaitable` is true. Type checkers still see the preferred
async contract so Protocol structural typing for :class:`SynapseAgent` stays
valid; callers that pass a sync callback may cast it to
:data:`MessageCallback`.
"""


class _DispatchAgent(Protocol):
    """Attributes required by the inbound dispatch mixin."""

    callback: MessageCallback | None
    hub_id: str
    hub_protocol_version: int | None
    mailbox: bool
    mailbox_advance: Callable[[dict[str, Any]], bool] | None
    mailbox_for: str
    name: str
    on_lease_granted: Callable[[str], None] | None
    owner_lease: str
    ready_event: Any
    verbose: bool
    _mailbox_since_seq: int

    async def ack(self, seq: int, *, mailbox_for: str = "") -> bool:
        """Acknowledge a delivered directed message by its durable seq."""

    async def _track_mailbox_frame(self, data: dict[str, Any]) -> None:
        """Advance the mailbox cursor and ack an accepted chat frame."""


async def invoke_message_callback(
    callback: MessageCallback | Callable[[dict[str, Any]], None],
    data: dict[str, Any],
) -> None:
    """Invoke ``callback`` and await only when it returned an awaitable.

    Parameters
    ----------
    callback :
        Preferred async coroutine callback, or a sync callable returning ``None``.
    data : dict[str, Any]
        The decoded inbound message.

    Raises
    ------
    BaseException
        Whatever the callback raises on invoke, or whatever awaiting its
        awaitable result raises. Nothing is caught here.
    """
    result: Any = cast(Any, callback)(data)
    if inspect.isawaitable(result):
        await result


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

        if data.get("type") == MessageType.LEASE_GRANTED:
            # The hub granted an ownership lease on the bound name. Record the
            # token — and hand it to the persistence hook immediately — because
            # the process may exit (a one-shot verb, a woken waiter) long before
            # any later frame arrives.
            token = str(data.get("owner_lease") or "")
            if token:
                self.owner_lease = token
                if self.on_lease_granted is not None:
                    self.on_lease_granted(token)

        if self.mailbox and data.get("type") == MessageType.CHAT:
            await self._track_mailbox_frame(data)

        # Ignore our own chat echoes, but still process system replies.
        if data.get("sender") == self.name and data.get("type") == MessageType.CHAT:
            return
        if self.callback is not None:
            await invoke_message_callback(self.callback, data)

    async def _track_mailbox_frame(self: _DispatchAgent, data: dict[str, Any]) -> None:
        """Advance the mailbox cursor and ack an accepted chat frame.

        Every chat frame carries its durable journal ``seq``; the cursor tracks the
        highest one seen so a reconnect resumes its backlog from there. Every live or
        replayed frame admitted by the acceptance gate is acknowledged by that ``seq``;
        the hub advances its receiver watermark and may also release a deferred delivery
        receipt. The ACK proves transport acceptance, not model processing. A missing
        or non-integer ``seq`` is ignored rather than allowed to reset the cursor.

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
        await self.ack(seq, mailbox_for=self.mailbox_for or self.name)
