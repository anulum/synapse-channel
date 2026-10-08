# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable record-only ingestion of native vendor messages
"""Journal the record of a message that travelled over a vendor's own channel.

The handler stores one ``native_message`` event and answers the recorder. It
delivers nothing: no mailbox entry, no delivery receipt, no wake. The hub
stamps who recorded it, when, and how that name was bound to the connection,
so a reader can weigh a record from an open loopback hub differently from one
written under a credential.
"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from synapse_channel.core.acl import EVIDENCE
from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.durable_ingress import DurableIngressQuota, chat_frame_bytes
from synapse_channel.core.journal import EventKind
from synapse_channel.core.native_message import (
    NativeMessageError,
    own_side_seat,
    parse_native_message_record,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_access import fixed_access
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from synapse_channel.core.handler_context import HandlerContext

__all__ = ["handle_native_message_record", "native_message_quota", "recorder_binding"]

QUOTA_EVENTS = 600
QUOTA_BYTES = 8_388_608
QUOTA_WINDOW_SECONDS = 60.0

_QUOTAS: WeakKeyDictionary[HandlerContext, DurableIngressQuota] = WeakKeyDictionary()
_JOURNAL_FAILURES = (sqlite3.Error, TypeError, ValueError, OSError)


def native_message_quota(hub: HandlerContext) -> DurableIngressQuota:
    """Return the hub's ingress quota for native-message records.

    The quota belongs to this verb, so it lives beside the handler and is
    created on first use instead of being another attribute of the hub.

    Parameters
    ----------
    hub : HandlerContext
        The coordination hub.

    Returns
    -------
    DurableIngressQuota
        One sliding-window quota per hub, keyed by the socket's principal.
    """
    quota = _QUOTAS.get(hub)
    if quota is None:
        quota = DurableIngressQuota(
            max_events=QUOTA_EVENTS,
            max_bytes=QUOTA_BYTES,
            window_seconds=QUOTA_WINDOW_SECONDS,
        )
        _QUOTAS[hub] = quota
    return quota


def recorder_binding(hub: HandlerContext, websocket: Any, principal: str) -> str:
    """Name how the recorder's identity was established on this connection.

    Parameters
    ----------
    hub : HandlerContext
        The coordination hub.
    websocket : Any
        The recorder's socket.
    principal : str
        The socket's server-derived quota principal.

    Returns
    -------
    str
        ``auth_token`` when the socket presented a credential,
        ``identity_proof`` when its registration was signed by a key the
        operator enrolled, otherwise ``socket_name``: the socket holds the
        name and nothing proves more.
    """
    if principal.startswith("auth-token:"):
        return "auth_token"
    if hub.clients.identity_proof(websocket) is not None:
        return "identity_proof"
    return "socket_name"


async def _reject(hub: HandlerContext, websocket: Any, sender: str, text: str, reason: str) -> None:
    await hub.send_json(
        websocket,
        hub.system(
            text,
            msg_type=MessageType.NATIVE_MESSAGE_REJECTED,
            target=sender,
            error_code=reason,
        ),
    )


async def handle_native_message_record(
    hub: HandlerContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Record one native message durably, or privately refuse it.

    Parameters
    ----------
    hub : HandlerContext
        The coordination hub.
    sender : str
        The name bound to the recorder's socket; stored as ``recorder``.
    data : dict[str, Any]
        The ``native_message_record`` frame. It must carry ``idem_key``.
    websocket : Any
        The recorder's transport, for the private reply.

    Notes
    -----
    Refusals, each a ``native_message_rejected`` reply with ``error_code``:
    ``native_record_unavailable`` (no journal, or the journal write failed),
    ``native_record_invalid`` and its text variants (schema),
    ``native_record_idem_key_required``, ``native_record_not_own_side`` (the
    recorder is not the seat on its own side of the message) and
    ``native_record_rate_limited``. A repeated key with the same content
    replays the first reply; with different content the hub answers its
    ordinary idempotency conflict.
    """
    if hub.journal is None:
        await _reject(
            hub,
            websocket,
            sender,
            "native message records require a durable hub",
            "native_record_unavailable",
        )
        return
    try:
        record = parse_native_message_record(data)
    except NativeMessageError as exc:
        await _reject(hub, websocket, sender, str(exc), exc.reason)
        return
    if not str(data.get("idem_key") or ""):
        await _reject(
            hub,
            websocket,
            sender,
            "native message records require an idem_key",
            "native_record_idem_key_required",
        )
        return
    if own_side_seat(record) != sender:
        await _reject(
            hub,
            websocket,
            sender,
            "a native message is recorded by the seat on its own side only",
            "native_record_not_own_side",
        )
        return
    principal = hub.clients.quota_principal(websocket, fallback_agent=sender)
    if native_message_quota(hub).allow(principal, nbytes=chat_frame_bytes(data)):
        await _reject(
            hub,
            websocket,
            sender,
            "native message record ingress limit exceeded",
            "native_record_rate_limited",
        )
        return
    record["recorder"] = sender
    record["recorder_binding"] = recorder_binding(hub, websocket, principal)
    record["recorded_at"] = time.time()

    def mutate(_state: Any) -> dict[str, Any]:
        return record

    def prepare(result: dict[str, Any]) -> OperationDraft:
        recorded = hub.system(
            "Native message recorded.",
            msg_type=MessageType.NATIVE_MESSAGE_RECORDED,
            target=sender,
            audit_seq=0,
            text_sha256=result["text_sha256"],
            phase=result["phase"],
            recorder_binding=result["recorder_binding"],
        )
        return OperationDraft(
            response=recorded,
            events=((EventKind.NATIVE_MESSAGE, result),),
            intent={
                "family": "native_message",
                "response_type": MessageType.NATIVE_MESSAGE_RECORDED,
            },
            response_event_seq_field="audit_seq",
        )

    try:
        execution = await hub.run_atomic_operation(data, mutate, prepare)
    except _JOURNAL_FAILURES:
        execution = None
    if execution is None or execution.response is None:
        await _reject(
            hub,
            websocket,
            sender,
            "native message record was not journalled",
            "native_record_unavailable",
        )
        return
    await hub.send_json(websocket, execution.response)
    # A replay or a conflict is answered before dispatch; one that arrives here lost a
    # race to the same operation, whose settlement this repeats without changing it.
    await hub.settle_atomic_operation(data)


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.NATIVE_MESSAGE_RECORD,),
        handler=handle_native_message_record,
        reply_types=(
            MessageType.NATIVE_MESSAGE_RECORDED,
            MessageType.NATIVE_MESSAGE_REJECTED,
        ),
        mutates=True,
        replay_protected=True,
        mutation_guarded=True,
        accesses=fixed_access(EVIDENCE, "evidence", "native-message"),
        event_kinds=(EventKind.NATIVE_MESSAGE,),
        minimum_wire_version=7,
        commands=("native-record",),
    ),
)
