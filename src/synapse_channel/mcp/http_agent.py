# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP native operation correlation
"""Carry remote retry identity through the existing signed hub client."""

from __future__ import annotations

from collections import OrderedDict
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


MAX_OPERATION_EPOCHS = 1024
"""Most recent keyed operations whose lease epoch is kept for an exact replay."""


class OperationEpochs:
    """Remember the lease epoch each keyed operation was first sent with.

    A retried operation must be byte-for-byte the same request, or the hub
    refuses the reused key. The client forgets a lease's epoch once the release
    is granted, so a retried release would otherwise go out without it. This
    bounded map keeps the first epoch per operation and re-applies it.
    """

    def __init__(self, limit: int = MAX_OPERATION_EPOCHS) -> None:
        self._limit = max(1, int(limit))
        self._epochs: OrderedDict[str, object] = OrderedDict()

    def apply(self, key: str, extra: dict[str, Any]) -> None:
        """Record ``extra``'s epoch under ``key``, or restore the recorded one."""
        if "epoch" in extra:
            self._epochs[key] = extra["epoch"]
            self._epochs.move_to_end(key)
            while len(self._epochs) > self._limit:
                self._epochs.popitem(last=False)
        elif key in self._epochs:
            extra["epoch"] = self._epochs[key]


class HttpHubAgent(SynapseAgent):
    """Preserve native signing and give remote mutations their stable retry key.

    The configured identity remains a provisioned hub principal. HTTP bearer
    material never enters this client. Chat retains native at-least-once delivery
    with receiver deduplication; board and lease operations use hub idempotency.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._operation_epochs = OperationEpochs()

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
            self._operation_epochs.apply(f"{msg_type}\0{operation}", extra)
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
