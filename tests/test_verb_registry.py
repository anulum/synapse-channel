# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — independent freeze and admission checks for handler-owned verbs
"""Pin registry-derived routing and guards independently of their declarations."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import MutableMapping
from dataclasses import replace

import pytest

from synapse_channel.core.acl_enforcement import GATED_MUTATIONS
from synapse_channel.core.handlers import DISPATCH, VERBS
from synapse_channel.core.hub_ledger_guard import _MUTATING_TYPES
from synapse_channel.core.journal import EventKind
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, WIRE_PROTOCOL_VERSION, MessageType
from synapse_channel.core.verb_registry import build_registry

_FROZEN_DISPATCH = {
    "ack": "messaging.handle_ack",
    "advertise": "offerings.handle_advertise",
    "attachment_abort": "attachments.handle_attachment",
    "attachment_begin": "attachments.handle_attachment",
    "attachment_chunk": "attachments.handle_attachment",
    "attachment_commit": "attachments.handle_attachment",
    "attachment_gc": "attachments.handle_attachment",
    "attachment_info": "attachments.handle_attachment",
    "attachment_peer_request": "attachment_peer.handle_attachment_peer",
    "attachment_read": "attachments.handle_attachment",
    "attachment_ref": "attachments.handle_attachment",
    "board_request": "snapshots.handle_board_request",
    "channel_create": "channels.handle_channel_create",
    "channel_history_request": "channels.handle_channel_history_request",
    "channel_invite": "channels.handle_channel_invite",
    "channel_join": "channels.handle_channel_join",
    "channel_leave": "channels.handle_channel_leave",
    "channel_list_request": "channels.handle_channel_list_request",
    "chat": "messaging.handle_chat",
    "checkpoint": "leasing.handle_checkpoint",
    "claim": "leasing.handle_claim",
    "dead_letter_forwarding": "dead_letter_forwarding.handle_dead_letter_forwarding",
    "delivery_ack": "delivery_modes.handle_delivery_stage",
    "delivery_boundary": "delivery_modes.handle_delivery_stage",
    "delivery_cancel": "delivery_modes.handle_delivery_cancel",
    "delivery_outcome": "delivery_modes.handle_delivery_stage",
    "delivery_request": "delivery_modes.handle_delivery_request",
    "delivery_status_request": "delivery_modes.handle_delivery_status_request",
    "entitlement_advert": "entitlement_adverts.handle_entitlement_advert",
    "federation_offer_request": "federation_offer.handle_federation_offer_request",
    "finding": "memory.handle_finding",
    "guard_denial": "guard_evidence.handle_guard_denial",
    "handoff": "leasing.handle_handoff",
    "heartbeat": "messaging.handle_heartbeat",
    "history_request": "snapshots.handle_history_request",
    "identity_enroll": "identity_enrollments.handle_identity_enroll",
    "identity_pin_reclaim": "identity_pins.handle_identity_pin_reclaim",
    "identity_revoke": "identity_enrollments.handle_identity_revoke",
    "ledger_progress": "planning.handle_ledger_progress",
    "ledger_task": "planning.handle_ledger_task",
    "ledger_task_update": "planning.handle_ledger_task_update",
    "manifest_request": "snapshots.handle_manifest_request",
    "multihub_claim_request": "multihub_claim.handle_multihub_claim_request",
    "multihub_log_request": "multihub.handle_multihub_log_request",
    "multihub_message_forward": "message_forward.handle_multihub_message_forward",
    "native_message_record": "native_message.handle_native_message_record",
    "offer_resource": "offerings.handle_resource",
    "operator_relay_request": "operator_relay.handle_operator_relay_request",
    "recall_log": "memory.handle_recall_log",
    "release": "leasing.handle_release",
    "resource": "offerings.handle_resource",
    "resource_offer": "offerings.handle_resource",
    "resume_request": "snapshots.handle_resume_request",
    "spend_request": "spend.handle_spend_request",
    "state_request": "snapshots.handle_state_request",
    "task_update": "leasing.handle_task_update",
    "wait_request": "leasing.handle_wait_request",
    "who_request": "snapshots.handle_who_request",
}

_FROZEN_REPLAY = frozenset(
    (
        "checkpoint",
        "claim",
        "finding",
        "guard_denial",
        "handoff",
        "ledger_progress",
        "ledger_task",
        "ledger_task_update",
        "native_message_record",
        "offer_resource",
        "recall_log",
        "release",
        "resource",
        "resource_offer",
        "task_update",
    )
)

_FROZEN_MUTATION_GUARD = frozenset(
    (
        "advertise",
        "attachment_abort",
        "attachment_begin",
        "attachment_chunk",
        "attachment_commit",
        "attachment_gc",
        "attachment_info",
        "attachment_read",
        "attachment_ref",
        "channel_create",
        "channel_invite",
        "channel_join",
        "channel_leave",
        "chat",
        "checkpoint",
        "claim",
        "delivery_request",
        "entitlement_advert",
        "finding",
        "guard_denial",
        "handoff",
        "identity_enroll",
        "identity_pin_reclaim",
        "identity_revoke",
        "ledger_progress",
        "ledger_task",
        "ledger_task_update",
        "native_message_record",
        "offer_resource",
        "release",
        "resource",
        "resource_offer",
        "task_update",
    )
)


def test_routing_preserves_every_existing_handler_and_alias() -> None:
    """Refuse replacement, omission or accidental additions to concrete routing."""
    actual = {
        name: f"{handler.__module__}.{handler.__name__}" for name, handler in DISPATCH.items()
    }
    assert actual == {
        name: f"synapse_channel.core.handlers.{qualified}"
        for name, qualified in _FROZEN_DISPATCH.items()
    }
    assert set(VERBS) == set(_FROZEN_DISPATCH)


def test_replay_and_journal_guards_preserve_their_separate_frozen_sets() -> None:
    """An omitted declaration must fail without relying on a shared computed expectation."""
    assert _MUTATING_TYPES == _FROZEN_REPLAY
    assert GATED_MUTATIONS == _FROZEN_MUTATION_GUARD


def test_collected_metadata_refers_to_existing_wire_and_journal_vocabulary() -> None:
    """Prevent declarations from advertising unavailable replies or event kinds."""
    wire = {value for key, value in vars(MessageType).items() if key.isupper()}
    events = {value for key, value in vars(EventKind).items() if key.isupper()}
    for name, spec in VERBS.items():
        assert name in wire | RESOURCE_TYPE_ALIASES
        assert set(spec.reply_types) <= wire
        assert set(spec.event_kinds) <= events
        assert 1 <= spec.minimum_wire_version <= WIRE_PROTOCOL_VERSION
        assert DISPATCH[name] is spec.handler


@pytest.mark.parametrize("requests", [(), ("",)])
def test_empty_request_declaration_refuses_startup(requests: tuple[str, ...]) -> None:
    """An unusable declared endpoint cannot enter the routing registry."""
    broken = replace(VERBS["native_message_record"], request_types=requests)
    with pytest.raises(ValueError, match="non-empty request"):
        build_registry(((broken,),))


@pytest.mark.parametrize("floor", [False, True, 0, WIRE_PROTOCOL_VERSION + 1])
def test_invalid_wire_floor_refuses_startup(floor: int) -> None:
    """Reject bool-as-version and an endpoint outside the supported vocabulary."""
    broken = replace(VERBS["native_message_record"], minimum_wire_version=floor)
    with pytest.raises(ValueError, match="wire floor"):
        build_registry(((broken,),))


def test_guarded_endpoint_without_access_mapping_refuses_startup() -> None:
    """Fail before routing rather than allowing a missing guarded ACL mapping."""
    broken = replace(VERBS["native_message_record"], accesses=None)
    with pytest.raises(ValueError, match="ACL mapping"):
        build_registry(((broken,),))


def test_duplicate_registration_refuses_instead_of_replacing_a_handler() -> None:
    """A later family cannot overwrite an earlier endpoint or resource alias."""
    first = VERBS["native_message_record"]
    colliding = replace(VERBS["claim"], request_types=("native_message_record",))
    with pytest.raises(ValueError, match="duplicate verb registration"):
        build_registry(((first,), (colliding,)))


def test_accepted_registry_cannot_be_mutated() -> None:
    """A writable consumer copy must not replace authoritative registry entries."""
    original = VERBS["chat"]
    assert not isinstance(VERBS, MutableMapping)
    consumer = dict(VERBS)
    consumer["chat"] = VERBS["claim"]
    assert consumer["chat"] is VERBS["claim"]
    assert VERBS["chat"] is original


@pytest.mark.parametrize(
    "module",
    [
        "synapse_channel.core.acl_enforcement",
        "synapse_channel.core.handlers.native_message",
        "synapse_channel.core.hub_ledger_guard",
        "synapse_channel.core.journal",
        "synapse_channel.core.protocol",
    ],
)
def test_cold_consumer_import_does_not_depend_on_a_warm_registry(module: str) -> None:
    """Exercise actual isolated process imports so cached modules cannot hide a cycle."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; print('ready')"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (result.returncode, result.stdout) == (0, "ready\n"), result.stderr
