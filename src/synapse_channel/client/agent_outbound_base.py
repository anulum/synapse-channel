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
import secrets
from collections.abc import Mapping
from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.identity_keys import sign_registration
from synapse_channel.core.message_auth import DEFAULT_SIGNED_MESSAGE_TYPES, sign_frame
from synapse_channel.core.protocol import MIN_ACK_PROTOCOL_VERSION, MessageType, build_envelope

__all__ = ["AgentSendMixin"]


class AgentSendMixin:
    """Send raw envelopes and chat messages."""

    async def send_message(
        self: _OutboundAgent,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        sign_identity: bool = False,
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
        sign_identity : bool, optional
            When ``True`` and an identity signing key is configured, attach an Ed25519
            identity signature so a hub that requires connection-identity binding admits
            this socket. Used only for the registration heartbeat — a keepalive tick
            leaves it ``False`` — so exactly the name-binding frame is proven.
        **extra : Any
            Additional protocol fields merged into the envelope.
        """
        if self.connection is None:
            return
        msg = build_envelope(self.name, msg_type, target=target, payload=payload, **extra)
        if self._message_auth_key is not None and msg_type in DEFAULT_SIGNED_MESSAGE_TYPES:
            msg.setdefault("idem_key", secrets.token_urlsafe(18))
            self._message_auth_sequence += 1
            msg = sign_frame(
                msg,
                key=self._message_auth_key,
                nonce=secrets.token_urlsafe(18),
                sequence=self._message_auth_sequence,
            )
        if sign_identity and self._identity_key is not None:
            self._identity_sequence += 1
            msg = sign_registration(
                msg,
                private_key=self._identity_key,
                key_id=self._identity_key_id,
                nonce=secrets.token_urlsafe(18),
                sequence=self._identity_sequence,
            )
        await self.connection.send(json.dumps(msg))

    async def chat(
        self: _OutboundAgent,
        payload: str,
        *,
        target: str = "all",
        priority: bool = False,
        memory_tag: str = "",
        channel: str = "",
        metadata: Mapping[str, Any] | None = None,
        client_msg_id: str = "",
    ) -> None:
        """Send a chat message to the room, a single agent, or a private channel.

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
        channel : str, optional
            Private channel id. When set, the hub delivers the message only to
            that channel's online members instead of broadcasting it, and refuses
            it if this agent is not a member.
        metadata : Mapping[str, Any] or None, optional
            Structured caller metadata forwarded as an opaque ``metadata`` field
            on the chat envelope. The hub preserves it for receivers and journal
            readers, but does not interpret it.
        client_msg_id : str, optional
            Stable sender-chosen identity for at-least-once retry deduplication.
            The hub echoes it; receivers deduplicate by ``(sender, client_msg_id)``.
        """
        extra: dict[str, Any] = {}
        if priority:
            extra["priority"] = True
        if memory_tag:
            extra["memory_tag"] = memory_tag
        if channel:
            extra["channel"] = channel
        if metadata is not None:
            extra["metadata"] = dict(metadata)
        if client_msg_id:
            extra["client_msg_id"] = client_msg_id
        await self.send_message(MessageType.CHAT, target=target, payload=payload, **extra)

    async def ack(self: _OutboundAgent, seq: int, *, mailbox_for: str = "") -> bool:
        """Acknowledge a delivered directed message by its durable ``seq``.

        A mailbox receiver emits this after its acceptance gate admits a live or
        replayed chat. The hub advances the logical mailbox watermark and, when
        the chat had a pending delivery receipt, tells the original sender it
        finally arrived. This acknowledges receiver transport acceptance, never
        model reading or action. It is emitted only when the hub advertised wire version
        :data:`~synapse_channel.core.protocol.MIN_ACK_PROTOCOL_VERSION` or newer in
        its ``WELCOME``, so a client never sends the verb to a hub too old to know
        it — the check makes the acknowledgement safe to call unconditionally.

        Parameters
        ----------
        seq : int
            The durable journal sequence number carried on the replayed frame.
        mailbox_for : str, optional
            Logical mailbox identity. A receive-only ``-rx`` sidecar supplies its
            bare owner; a directly connected agent may omit it.

        Returns
        -------
        bool
            ``True`` when the ack was sent, ``False`` when the hub predates the ack
            verb (or never advertised a version) and it was withheld.
        """
        if (self.hub_protocol_version or 0) < MIN_ACK_PROTOCOL_VERSION:
            return False
        extra = {"mailbox_for": mailbox_for} if mailbox_for else {}
        await self.send_message(MessageType.ACK, seq=seq, **extra)
        return True

    async def channel_create(self: _OutboundAgent, channel: str, *, label: str = "") -> None:
        """Create a private channel owned by this agent (its first member)."""
        await self.send_message(MessageType.CHANNEL_CREATE, channel=channel, label=label)

    async def channel_invite(self: _OutboundAgent, channel: str, invitee: str) -> None:
        """Invite ``invitee`` to a channel this agent owns (one-time join grant)."""
        await self.send_message(MessageType.CHANNEL_INVITE, channel=channel, invitee=invitee)

    async def channel_join(self: _OutboundAgent, channel: str) -> None:
        """Join this agent to a private channel it was invited to."""
        await self.send_message(MessageType.CHANNEL_JOIN, channel=channel)

    async def channel_leave(self: _OutboundAgent, channel: str) -> None:
        """Remove this agent from a private channel."""
        await self.send_message(MessageType.CHANNEL_LEAVE, channel=channel)

    async def request_channels(self: _OutboundAgent) -> None:
        """Request the list of channels this agent is a member of."""
        await self.send_message(MessageType.CHANNEL_LIST_REQUEST)

    async def request_channel_history(
        self: _OutboundAgent, channel: str, *, limit: int = 20
    ) -> None:
        """Request retained live history for a private channel this agent belongs to."""
        await self.send_message(MessageType.CHANNEL_HISTORY_REQUEST, channel=channel, limit=limit)
