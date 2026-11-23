# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared outbound client protocol types
"""Shared outbound protocol types for the reusable client."""

from __future__ import annotations

from typing import Any, Protocol

from websockets.asyncio.client import ClientConnection

from synapse_channel.client.agent_lease_memory import AgentLeaseMemory
from synapse_channel.client.agent_signing_identity import AgentSigningIdentity
from synapse_channel.client.frame_signing_client import ClientFrameSession

__all__ = ["_OutboundAgent"]


class _OutboundAgent(Protocol):
    """Attributes required to serialise and send outbound envelopes."""

    connection: ClientConnection | None
    name: str
    hub_protocol_version: int | None
    lease_memory: AgentLeaseMemory
    hub_id: str
    frame_session: ClientFrameSession | None
    signing: AgentSigningIdentity

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one message envelope to the hub."""
