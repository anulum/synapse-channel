# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — message-type dispatch registry for the hub
"""Declarative dispatch table mapping each message type to its handler.

The hub's routing core looks a parsed, sender-resolved message up in
:data:`DISPATCH` and awaits the matched handler, falling back to an
unknown-type error when there is no entry. Each handler is a free coroutine that
takes the hub as its first argument and reaches the shared state, journal, and
transport through it — so adding a verb is one table entry plus one function, and
the routing core stays a lookup rather than a growing ``if`` ladder. Every
resource alias maps to the single resource handler.

Handlers consume the internal structural ``HandlerContext`` contract or a
family extension, including capabilities required by their callees. ``Handler``
keeps the concrete ``SynapseHub`` argument so the type checker verifies each
registered handler against the actual hub. Contracts and their imports exist
only under ``TYPE_CHECKING``; dispatch still invokes the live hub's methods.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from synapse_channel.core.handlers.attachment_peer import handle_attachment_peer
from synapse_channel.core.handlers.attachments import handle_attachment
from synapse_channel.core.handlers.channels import (
    handle_channel_create,
    handle_channel_history_request,
    handle_channel_invite,
    handle_channel_join,
    handle_channel_leave,
    handle_channel_list_request,
)
from synapse_channel.core.handlers.dead_letter_forwarding import handle_dead_letter_forwarding
from synapse_channel.core.handlers.delivery_modes import (
    handle_delivery_cancel,
    handle_delivery_request,
    handle_delivery_stage,
    handle_delivery_status_request,
)
from synapse_channel.core.handlers.entitlement_adverts import handle_entitlement_advert
from synapse_channel.core.handlers.federation_offer import handle_federation_offer_request
from synapse_channel.core.handlers.guard_evidence import handle_guard_denial
from synapse_channel.core.handlers.identity_enrollments import (
    handle_identity_enroll,
    handle_identity_revoke,
)
from synapse_channel.core.handlers.identity_pins import handle_identity_pin_reclaim
from synapse_channel.core.handlers.leasing import (
    handle_checkpoint,
    handle_claim,
    handle_handoff,
    handle_release,
    handle_task_update,
    handle_wait_request,
)
from synapse_channel.core.handlers.memory import handle_finding, handle_recall_log
from synapse_channel.core.handlers.message_forward import handle_multihub_message_forward
from synapse_channel.core.handlers.messaging import handle_ack, handle_chat, handle_heartbeat
from synapse_channel.core.handlers.multihub import handle_multihub_log_request
from synapse_channel.core.handlers.multihub_claim import handle_multihub_claim_request
from synapse_channel.core.handlers.native_message import handle_native_message_record
from synapse_channel.core.handlers.offerings import handle_advertise, handle_resource
from synapse_channel.core.handlers.operator_relay import handle_operator_relay_request
from synapse_channel.core.handlers.planning import (
    handle_ledger_progress,
    handle_ledger_task,
    handle_ledger_task_update,
)
from synapse_channel.core.handlers.snapshots import (
    handle_board_request,
    handle_history_request,
    handle_manifest_request,
    handle_resume_request,
    handle_state_request,
    handle_who_request,
)
from synapse_channel.core.handlers.spend import handle_spend_request
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

Handler = Callable[["SynapseHub", str, dict[str, Any], Any], Awaitable[None]]
"""A message handler: ``(hub, sender, data, websocket) -> awaitable[None]``."""

DISPATCH: dict[str, Handler] = {
    MessageType.ATTACHMENT_PEER_REQUEST: handle_attachment_peer,
    MessageType.ATTACHMENT_BEGIN: handle_attachment,
    MessageType.ATTACHMENT_CHUNK: handle_attachment,
    MessageType.ATTACHMENT_COMMIT: handle_attachment,
    MessageType.ATTACHMENT_ABORT: handle_attachment,
    MessageType.ATTACHMENT_INFO: handle_attachment,
    MessageType.ATTACHMENT_READ: handle_attachment,
    MessageType.ATTACHMENT_REF: handle_attachment,
    MessageType.ATTACHMENT_GC: handle_attachment,
    MessageType.CHAT: handle_chat,
    MessageType.ACK: handle_ack,
    MessageType.DELIVERY_REQUEST: handle_delivery_request,
    MessageType.DELIVERY_BOUNDARY: handle_delivery_stage,
    MessageType.DELIVERY_ACK: handle_delivery_stage,
    MessageType.DELIVERY_OUTCOME: handle_delivery_stage,
    MessageType.DELIVERY_CANCEL: handle_delivery_cancel,
    MessageType.DELIVERY_STATUS_REQUEST: handle_delivery_status_request,
    MessageType.HEARTBEAT: handle_heartbeat,
    MessageType.CLAIM: handle_claim,
    MessageType.RELEASE: handle_release,
    MessageType.STATE_REQUEST: handle_state_request,
    MessageType.WHO_REQUEST: handle_who_request,
    MessageType.HISTORY_REQUEST: handle_history_request,
    MessageType.RESUME_REQUEST: handle_resume_request,
    MessageType.WAIT_REQUEST: handle_wait_request,
    MessageType.TASK_UPDATE: handle_task_update,
    MessageType.HANDOFF: handle_handoff,
    MessageType.CHECKPOINT: handle_checkpoint,
    MessageType.LEDGER_TASK: handle_ledger_task,
    MessageType.LEDGER_TASK_UPDATE: handle_ledger_task_update,
    MessageType.LEDGER_PROGRESS: handle_ledger_progress,
    MessageType.BOARD_REQUEST: handle_board_request,
    MessageType.ADVERTISE: handle_advertise,
    MessageType.MANIFEST_REQUEST: handle_manifest_request,
    MessageType.RECALL_LOG: handle_recall_log,
    MessageType.FINDING: handle_finding,
    MessageType.CHANNEL_CREATE: handle_channel_create,
    MessageType.CHANNEL_INVITE: handle_channel_invite,
    MessageType.CHANNEL_JOIN: handle_channel_join,
    MessageType.CHANNEL_LEAVE: handle_channel_leave,
    MessageType.CHANNEL_LIST_REQUEST: handle_channel_list_request,
    MessageType.CHANNEL_HISTORY_REQUEST: handle_channel_history_request,
    MessageType.MULTIHUB_LOG_REQUEST: handle_multihub_log_request,
    MessageType.MULTIHUB_CLAIM_REQUEST: handle_multihub_claim_request,
    MessageType.SPEND_REQUEST: handle_spend_request,
    MessageType.MULTIHUB_MESSAGE_FORWARD: handle_multihub_message_forward,
    MessageType.OPERATOR_RELAY_REQUEST: handle_operator_relay_request,
    MessageType.DEAD_LETTER_FORWARDING: handle_dead_letter_forwarding,
    MessageType.FEDERATION_OFFER_REQUEST: handle_federation_offer_request,
    MessageType.IDENTITY_PIN_RECLAIM: handle_identity_pin_reclaim,
    MessageType.IDENTITY_ENROLL: handle_identity_enroll,
    MessageType.IDENTITY_REVOKE: handle_identity_revoke,
    MessageType.ENTITLEMENT_ADVERT: handle_entitlement_advert,
    MessageType.GUARD_DENIAL: handle_guard_denial,
    MessageType.NATIVE_MESSAGE_RECORD: handle_native_message_record,
    **{alias: handle_resource for alias in RESOURCE_TYPE_ALIASES},
}

__all__ = ["DISPATCH", "Handler"]
