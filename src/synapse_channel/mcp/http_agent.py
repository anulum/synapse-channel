# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP native operation correlation
"""Carry remote retry identity through the existing signed hub client."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType

HTTP_OPERATION_ID: ContextVar[str] = ContextVar("synapse_http_operation_id", default="")
"""Stable caller operation identity, isolated to the current MCP request task."""

KEYED_MUTATIONS = frozenset(
    {
        MessageType.CLAIM,
        MessageType.RELEASE,
        MessageType.HANDOFF,
        MessageType.LEDGER_TASK,
        MessageType.LEDGER_TASK_UPDATE,
    }
)


class HttpHubAgent(SynapseAgent):
    """Preserve native signing and give remote mutations their stable retry key.

    The configured identity remains a provisioned hub principal. HTTP bearer
    material never enters this client. Chat retains native at-least-once delivery
    with receiver deduplication; board and lease operations use hub idempotency.
    """

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        sign_identity: bool = False,
        **extra: Any,
    ) -> None:
        """Enrich one native envelope without replacing its authentication path.

        Parameters
        ----------
        msg_type : str
            Native hub protocol verb.
        target, payload : str
            Original recipient and content.
        sign_identity : bool
            Native registration identity-signature switch.
        **extra : Any
            Original protocol fields; explicit caller correlation is retained.
        """
        operation = HTTP_OPERATION_ID.get()
        if operation and msg_type in KEYED_MUTATIONS:
            extra["idem_key"] = operation
        if operation and msg_type == MessageType.CHAT:
            extra["client_msg_id"] = operation
        if operation and msg_type == MessageType.LEDGER_TASK:
            extra["project"] = self.name.split("/", 1)[0]
        await super().send_message(
            msg_type,
            target=target,
            payload=payload,
            sign_identity=sign_identity,
            **extra,
        )
