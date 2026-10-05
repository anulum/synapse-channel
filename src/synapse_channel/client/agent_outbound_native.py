# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client call that records a native vendor message
"""Outbound native-message record helper for the reusable client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.native_message import (
    native_idempotency_key,
    parse_native_message_record,
)
from synapse_channel.core.protocol import (
    MIN_NATIVE_MESSAGE_RECORD_PROTOCOL_VERSION,
    MessageType,
)

__all__ = ["AgentNativeMessageMixin"]


class AgentNativeMessageMixin:
    """Send the record of a message that travelled over a vendor's own channel."""

    async def record_native_message(
        self: _OutboundAgent,
        record: Mapping[str, Any],
        *,
        idem_key: str | None = None,
    ) -> str:
        """Send one ``native_message_record`` frame to the hub.

        The record is validated before anything is sent, and it is sent only to
        a hub that advertises the verb, so a caller never mistakes an old hub's
        unknown-type answer for a stored record. The verdict arrives through the
        agent's ordinary callback as ``native_message_recorded`` or
        ``native_message_rejected``.

        Parameters
        ----------
        record : Mapping[str, Any]
            Record fields as documented for the verb: ``channel``,
            ``direction``, ``phase``, the seats, ``sent_at``, ``text_sha256``,
            ``text_bytes`` and the optional ones.
        idem_key : str or None, optional
            Retry key. Derived from the record when omitted, so a repeated call
            for the same message is applied once.

        Returns
        -------
        str
            The retry key the frame carried.

        Raises
        ------
        ValueError
            When the hub is older than wire version seven.
        NativeMessageError
            When the record is malformed.
        """
        if (self.hub_protocol_version or 0) < MIN_NATIVE_MESSAGE_RECORD_PROTOCOL_VERSION:
            raise ValueError("hub does not advertise native message records (wire version seven)")
        parsed = parse_native_message_record(dict(record))
        key = idem_key or native_idempotency_key(parsed)
        fields = {name: value for name, value in parsed.items() if value is not None}
        await self.send_message(
            MessageType.NATIVE_MESSAGE_RECORD, target="System", idem_key=key, **fields
        )
        return key
