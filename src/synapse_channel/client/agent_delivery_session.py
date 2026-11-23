# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the welcome, mailbox cursor and delivery incarnation
"""The delivery session one connection binds, and the mailbox cursor it resumes."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from typing import Any

from synapse_channel.client.frame_signing_client import ClientFrameSession, session_from_welcome
from synapse_channel.core.delivery_modes import MODES, QUALITIES
from synapse_channel.core.protocol import read_protocol_version


class AgentDeliverySession:
    """Own the welcome binding, the mailbox cursor and the delivery incarnation.

    The constructor keywords are the ones ``SynapseAgent`` already accepts.
    A welcome binds the hub, the protocol version and the signing session.
    A delivery-session frame binds the incarnation. The mailbox cursor survives
    a reconnect; the signing session does not.
    """

    def __init__(
        self,
        *,
        mailbox: bool,
        mailbox_since_seq: int,
        mailbox_for: str,
        mailbox_advance: Callable[[dict[str, Any]], bool] | None,
        delivery_capabilities: dict[str, str] | None,
    ) -> None:
        self.hub_id = "unknown"
        self.hub_protocol_version: int | None = None
        self.frame_session: ClientFrameSession | None = None
        self.delivery_incarnation = ""
        self.delivery_ready_event = asyncio.Event()
        if delivery_capabilities is not None and any(
            mode not in MODES or quality not in QUALITIES
            for mode, quality in delivery_capabilities.items()
        ):
            raise ValueError("delivery capabilities must name supported modes and qualities")
        self.delivery_capabilities = (
            None if delivery_capabilities is None else dict(delivery_capabilities)
        )
        self._delivery_session_token = (
            None if delivery_capabilities is None else secrets.token_hex(32)
        )
        self.mailbox = bool(mailbox)
        self._mailbox_since_seq = max(int(mailbox_since_seq), -1)
        self.mailbox_for = str(mailbox_for)
        self.mailbox_advance = mailbox_advance

    @property
    def mailbox_cursor(self) -> int:
        """Return the highest durable journal sequence this session has processed."""
        return self._mailbox_since_seq

    def registration_fields(self) -> dict[str, Any]:
        """Return the delivery token and capabilities for a registration heartbeat.

        An agent with no delivery capabilities sends neither field.
        """
        if self._delivery_session_token is None:
            return {}
        return {
            "delivery_session_token": self._delivery_session_token,
            "delivery_capabilities": self.delivery_capabilities,
        }

    def heartbeat_fields(self) -> dict[str, Any]:
        """Return the registration fields that ask for a mailbox replay.

        A negative cursor means the file is missing. The hub then anchors at its
        tip instead of replaying personal history from sequence 0. The mapping
        is empty when mailbox replay is off.
        """
        if not self.mailbox:
            return {}
        fields: dict[str, Any] = {"mailbox": True}
        if self._mailbox_since_seq < 0:
            fields["mailbox_cursor"] = "absent"
        else:
            fields["since_seq"] = self._mailbox_since_seq
        if self.mailbox_for:
            fields["mailbox_for"] = self.mailbox_for
        return fields

    def bind_welcome(self, data: dict[str, Any]) -> None:
        """Bind the hub, the protocol version and the signing session from a welcome."""
        self.frame_session = session_from_welcome(data)
        self.hub_id = str(data.get("hub_id", "unknown"))
        self.hub_protocol_version = read_protocol_version(data.get("protocol_version"))

    def bind_incarnation(self, incarnation: object) -> None:
        """Bind a 64-character lower-case hex incarnation and mark delivery ready.

        Any other value leaves the incarnation and the ready event unchanged.
        """
        if (
            isinstance(incarnation, str)
            and len(incarnation) == 64
            and all(character in "0123456789abcdef" for character in incarnation)
        ):
            self.delivery_incarnation = incarnation
            self.delivery_ready_event.set()

    def advance_cursor(self, seq: int) -> None:
        """Move the mailbox cursor forward when ``seq`` is newer."""
        if seq > self._mailbox_since_seq:
            self._mailbox_since_seq = seq

    def end_attempt(self) -> None:
        """Clear the incarnation and its ready event when an attempt ends.

        The signing session stays until the next attempt resets it, which is
        what a closed connection did before this object existed.
        """
        self.delivery_ready_event.clear()
        self.delivery_incarnation = ""

    def reset_attempt(self) -> None:
        """Clear the incarnation, the ready event and the signing session.

        The mailbox cursor, the hub id and the protocol version survive, as
        they did when this state lived on the agent.
        """
        self.end_attempt()
        self.frame_session = None
