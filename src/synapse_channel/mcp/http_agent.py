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
from synapse_channel.mcp.http_operation_epochs import (
    MAX_OPERATION_EPOCHS as MAX_OPERATION_EPOCHS,
)
from synapse_channel.mcp.http_operation_epochs import (
    HttpOperationEpochStore,
)

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


class OperationEpochs:
    """Remember the lease epoch each keyed operation was first sent with.

    A retried operation must be byte-for-byte the same request, or the hub
    refuses the reused key. The client forgets a lease's epoch once the release
    is granted, so a retried release would otherwise go out without it. This
    bounded map keeps the first epoch per operation and re-applies it. Provisioned
    HTTP clients also retain a bounded private disk cache across adapter restarts.

    Parameters
    ----------
    limit : int, optional
        In-memory hint count, oldest first; at least one entry is retained.
    store : HttpOperationEpochStore or None, optional
        Durable native-fence hints, checked before any HTTP mutation is sent.
    """

    def __init__(
        self, limit: int = MAX_OPERATION_EPOCHS, *, store: HttpOperationEpochStore | None = None
    ) -> None:
        self.store = store
        self._limit = max(1, int(limit))
        self._epochs: OrderedDict[str, object] = OrderedDict()

    def apply(self, key: str, extra: dict[str, Any], *, hub_id: str = "") -> None:
        """Retain the original fence, durably for a provisioned HTTP client.

        Parameters
        ----------
        key : str
            Native message type and the stable caller operation identifier.
        extra : dict[str, Any]
            Outgoing business fields; a retained original fence is restored here.
        hub_id : str, optional
            Actual admitted hub identity, required when a durable store is used.

        Raises
        ------
        ValueError, OSError
            When the durable cache cannot retain an exact valid hint.
        """
        if self.store is not None:
            epoch = self.store.bind(hub_id, key, extra.get("epoch"))
            if epoch is not None:
                extra["epoch"] = epoch
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
        """Bind the native transport and its fixed identity's durable retry cache.

        Parameters
        ----------
        *args : Any
            Native SynapseAgent positional arguments, starting with its exact name.
        **kwargs : Any
            Native transport and operator signing-key configuration.
        """
        super().__init__(*args, **kwargs)
        scope = "\0".join(
            (
                self.name,
                str(kwargs.get("identity_key_path") or ""),
                str(kwargs.get("identity_key_id") or ""),
            )
        )
        self._operation_epochs = OperationEpochs(store=HttpOperationEpochStore(scope))

    def prepare_release(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        """Prepare the original HTTP intent for an exact read-only outcome inquiry.

        Parameters
        ----------
        task_id : str
            Project task whose native receipt is requested.
        **kwargs : Any
            Existing SynapseAgent release fields, validated by the shared tool.

        Returns
        -------
        dict[str, Any]
            Native release frame with the caller key and retained original fence.
        """
        request = super().prepare_release(task_id, **kwargs)
        operation = HTTP_OPERATION_ID.get()
        if operation:
            request["idem_key"] = operation
            self._operation_epochs.apply(
                f"{MessageType.RELEASE}\0{operation}", request, hub_id=self.hub_id
            )
        return request

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
            self._operation_epochs.apply(f"{msg_type}\0{operation}", extra, hub_id=self.hub_id)
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
