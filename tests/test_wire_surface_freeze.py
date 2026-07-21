# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — freeze the on-wire surface a 1.0.0 release must lock
"""Freeze the on-wire message vocabulary and envelope shape ahead of 1.0.0.

The wire is the contract between every hub, client, and out-of-tree consumer: a
message's ``type`` string selects its handler, so renaming, removing, or
re-valuing any :class:`~synapse_channel.core.protocol.MessageType` silently
breaks every peer that still speaks the old wire. The capability manifest pins
the *count* of message types, but a count is blind to a rename that keeps the
count constant. This module pins the **complete name→value map**, so any rename,
removal, value change, or unreviewed addition fails CI.

It is the wire half of the pre-1.0 surface freeze: ``test_public_api`` freezes
the Python ``__all__``, ``test_federation_consumer_contract`` the deep federation
primitives out-of-tree consumers import, and this the wire message vocabulary,
the envelope's reserved keys, and the wire-protocol version. Before 1.0.0, adding
a message type is a reviewed edit HERE plus a capability-snapshot regen; at 1.0.0
the map is locked and a change must bump ``WIRE_PROTOCOL_VERSION``.
"""

from __future__ import annotations

from synapse_channel.core.protocol import (
    WIRE_PROTOCOL_VERSION,
    MessageType,
    build_envelope,
)

# The complete, frozen wire vocabulary — every MessageType name → its on-wire string.
# A change here is a wire-compatibility decision: a rename or re-value breaks every
# peer speaking the old wire and must bump WIRE_PROTOCOL_VERSION; an addition is a
# reviewed edit (plus a capability-snapshot regen) until 1.0.0 locks the map.
_FROZEN_WIRE_VALUES: dict[str, str] = {
    "ACK": "ack",
    "ADVERTISE": "advertise",
    "AUTH_DENIED": "auth_denied",
    "BOARD_REQUEST": "board_request",
    "BOARD_SNAPSHOT": "board_snapshot",
    "CAPABILITY_ADVERTISED": "capability_advertised",
    "CHANNEL_CREATE": "channel_create",
    "CHANNEL_HISTORY": "channel_history",
    "CHANNEL_HISTORY_REQUEST": "channel_history_request",
    "CHANNEL_INVITE": "channel_invite",
    "CHANNEL_JOIN": "channel_join",
    "CHANNEL_LEAVE": "channel_leave",
    "CHANNEL_LIST": "channel_list",
    "CHANNEL_LIST_REQUEST": "channel_list_request",
    "CHANNEL_RESULT": "channel_result",
    "CHAT": "chat",
    "CHECKPOINT": "checkpoint",
    "CHECKPOINT_DENIED": "checkpoint_denied",
    "CHECKPOINT_SAVED": "checkpoint_saved",
    "CLAIM": "claim",
    "CLAIM_DENIED": "claim_denied",
    "CLAIM_GRANTED": "claim_granted",
    "DEAD_LETTER_ESCALATION": "dead_letter_escalation",
    "DEAD_LETTER_FORWARDING": "dead_letter_forwarding",
    "DARK_SEAT_ALERT": "dark_seat_alert",
    "DELIVERY_RECEIPT": "delivery_receipt",
    "ERROR": "error",
    "FEDERATION_OFFER": "federation_offer",
    "FEDERATION_OFFER_REQUEST": "federation_offer_request",
    "FINDING": "finding",
    "FINDING_RECORDED": "finding_recorded",
    "FINDING_REJECTED": "finding_rejected",
    "GUARD_DENIAL": "guard_denial",
    "GUARD_DENIAL_RECORDED": "guard_denial_recorded",
    "HANDOFF": "handoff",
    "HANDOFF_DENIED": "handoff_denied",
    "HANDOFF_GRANTED": "handoff_granted",
    "HEARTBEAT": "heartbeat",
    "HISTORY_REQUEST": "history_request",
    "HISTORY_SNAPSHOT": "history_snapshot",
    "IDENTITY_PIN_RECLAIM": "identity_pin_reclaim",
    "IDENTITY_PIN_RECLAIM_RESULT": "identity_pin_reclaim_result",
    "LEASE_GRANTED": "lease_granted",
    "LEDGER_PROGRESS": "ledger_progress",
    "LEDGER_PROGRESS_POSTED": "ledger_progress_posted",
    "LEDGER_TASK": "ledger_task",
    "LEDGER_TASK_POSTED": "ledger_task_posted",
    "LEDGER_TASK_UPDATE": "ledger_task_update",
    "LEDGER_TASK_UPDATED": "ledger_task_updated",
    "MANIFEST_REQUEST": "manifest_request",
    "MANIFEST_SNAPSHOT": "manifest_snapshot",
    "MULTIHUB_CLAIM_REQUEST": "multihub_claim_request",
    "MULTIHUB_CLAIM_RESULT": "multihub_claim_result",
    "MULTIHUB_LOG_REQUEST": "multihub_log_request",
    "MULTIHUB_LOG_SNAPSHOT": "multihub_log_snapshot",
    "NAME_CONFLICT": "name_conflict",
    "OPERATOR_RELAY_REQUEST": "operator_relay_request",
    "OPERATOR_RELAY_RESULT": "operator_relay_result",
    "PRESENCE_UPDATE": "presence_update",
    "RECALL_LOG": "recall_log",
    "RECALL_LOGGED": "recall_logged",
    "RECIPIENT_LIVENESS_WARNING": "recipient_liveness_warning",
    "RELEASE": "release",
    "RELEASE_DENIED": "release_denied",
    "RELEASE_GRANTED": "release_granted",
    "RESOURCE": "resource",
    "RESOURCE_OFFERED": "resource_offered",
    "RESUME_REQUEST": "resume_request",
    "RESUME_SNAPSHOT": "resume_snapshot",
    "STATE_REQUEST": "state_request",
    "STATE_SNAPSHOT": "state_snapshot",
    "SYSTEM": "system",
    "TASK_UPDATE": "task_update",
    "TASK_UPDATED": "task_updated",
    "WAIT_DENIED": "wait_denied",
    "WAIT_GRANTED": "wait_granted",
    "WAIT_REQUEST": "wait_request",
    "WELCOME": "welcome",
    "WHO_REQUEST": "who_request",
    "WHO_SNAPSHOT": "who_snapshot",
}

# The reserved envelope keys every frame carries; a consumer parses on these, and
# build_envelope rejects a payload that reuses one (they cannot be spread flat).
_RESERVED_ENVELOPE_KEYS = frozenset({"sender", "target", "type", "payload", "timestamp"})


def _current_wire_values() -> dict[str, str]:
    """Return the live MessageType name→value map (public string constants only)."""
    return {
        name: value
        for name, value in vars(MessageType).items()
        if not name.startswith("_") and isinstance(value, str)
    }


def test_wire_message_vocabulary_is_frozen() -> None:
    # Exact match: a rename, removal, or value change fails, and adding a type
    # without updating this pin fails — every wire change is a reviewed edit here.
    assert _current_wire_values() == _FROZEN_WIRE_VALUES


def test_every_frozen_wire_value_is_a_distinct_string() -> None:
    # Two types sharing one wire string would make the `type` field ambiguous.
    values = list(_FROZEN_WIRE_VALUES.values())
    assert len(values) == len(set(values))


def test_wire_values_are_lower_snake_case() -> None:
    # The wire strings are stable lower_snake_case tokens, never display text.
    for value in _FROZEN_WIRE_VALUES.values():
        assert value == value.lower()
        assert " " not in value


def test_wire_envelope_carries_the_reserved_keys() -> None:
    envelope = build_envelope("USER", MessageType.CHAT, payload="hi", now=1700.0)
    assert _RESERVED_ENVELOPE_KEYS <= set(envelope)
    assert envelope["type"] == MessageType.CHAT
    assert envelope["sender"] == "USER"


def test_wire_protocol_version_is_frozen_at_the_current_baseline() -> None:
    # The wire is at version 2 (the ACK verb and its deferred delivery receipt); a
    # bump is a wire vocabulary change and a deliberate edit, not an accident.
    assert WIRE_PROTOCOL_VERSION == 2
