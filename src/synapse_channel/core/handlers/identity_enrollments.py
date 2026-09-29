# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — handle the governed identity-key enrolment verb (SOL4-ID-01)
"""Handle ``identity_enroll``: add or rotate an identity key under a proven operator.

The order of gates and the pure policy live in
:mod:`synapse_channel.core.identity_enrollments`. This handler collects the
observations the policy needs from the hub, writes the durable audit before the
store changes, persists the hub-owned enrolment store, swaps the effective trust
bundle into the identity gate, and answers the operator privately. A new key is
usable at the next registration, with no restart. Rotating another name's key
detaches that name's live socket, which proved the superseded key.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.acl import IDENTITY_ENROLL, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.identity_enrollments import (
    ENROLLER_ROLE,
    EnrollmentRequest,
    decode_public_key,
    enrollment_denial,
    merge_enrolled_keys,
    write_enrolled_keys,
)
from synapse_channel.core.journal import EventKind, record_identity_enrollment
from synapse_channel.core.message_auth import EventSignatureKey, EventSignatureTrustBundle
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.hub")

KEY_ROTATED_CLOSE_CODE = 4018
"""Close code sent to a live socket whose identity key an operator rotated."""


async def handle_identity_enroll(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Enrol, or rotate, one identity key after every governance gate passes."""
    expires_at = data.get("expires_at")
    request = EnrollmentRequest(
        requester=sender,
        name=str(data.get("name") or "").strip(),
        key_id=str(data.get("key_id") or "").strip(),
        public_key=decode_public_key(data.get("public_key")),
        reason=str(data.get("reason") or ""),
        expected_key_id=str(data.get("expected_key_id") or "").strip(),
        expires_at=expires_at,
    )
    static = hub._static_identity_trust
    enabled = (
        hub.identity_enrollment_path is not None and hub.journal is not None and static is not None
    )
    requester_pin = hub._identity_pins.pinned(sender)
    requester_bound = hub.require_identity_binding or requester_pin is not None
    acl_allowed = _acl_allows(hub, sender, request.name)
    role_granted = hub.role_grants is not None and hub.role_grants.may_claim(
        sender, f"{project_of(sender)}/{ENROLLER_ROLE}"
    )
    moment = hub._clock()
    denial = enrollment_denial(
        request,
        enabled=enabled,
        requester_bound=requester_bound,
        acl_allowed=acl_allowed,
        role_granted=role_granted,
        namespace_allowed=project_of(request.name) in hub.identity_enrollment_namespaces,
        rate_allowed=hub._enrollment_rate.allows(sender, now=moment),
        static=static.keys if static is not None else {},
        enrolled=hub._enrolled_identity_keys,
        now=time.time(),
    )
    authorised = enabled and requester_bound and acl_allowed and role_granted
    provenance: dict[str, Any] = {
        "operator": sender,
        "operator_key_id": requester_pin.key_id if requester_pin is not None else "bundle",
        "name": request.name,
        "key_id": request.key_id,
        "public_key_sha256": (
            hashlib.sha256(request.public_key).hexdigest() if request.public_key else ""
        ),
        "expected_key_id": request.expected_key_id,
        "reason": request.reason.strip(),
    }
    if denial:
        logger.warning(
            "identity enrolment denied operator=%s name=%s key_id=%s detail=%s",
            sender,
            request.name,
            request.key_id,
            denial,
        )
        audit_seq = None
        if authorised:
            # Only an authorised operator's refusals are journalled, so an unproven
            # socket cannot fill the durable audit trail.
            audit_seq = record_identity_enrollment(
                cast(EventStore, hub.journal),
                {**provenance, "status": "denied", "applied": False, "detail": denial},
            )
        await _send_result(hub, websocket, sender, request, False, denial, audit_seq)
        return

    journal = cast(EventStore, hub.journal)  # availability is part of ``enabled``
    approved_seq = record_identity_enrollment(
        journal, {**provenance, "status": "approved", "applied": False}
    )
    updated = dict(hub._enrolled_identity_keys)
    if request.expected_key_id:
        updated[request.expected_key_id] = replace(updated[request.expected_key_id], revoked=True)
    updated[request.key_id] = EventSignatureKey(
        key_id=request.key_id,
        public_key=cast(bytes, request.public_key),  # validated by the policy
        senders=frozenset({request.name}),
        expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
    )
    try:
        write_enrolled_keys(cast(Path, hub.identity_enrollment_path), updated)
    except OSError as exc:
        detail = f"could not persist the enrolment store: {exc}"
        record_identity_enrollment(
            journal,
            {
                **provenance,
                "status": "not_applied",
                "applied": False,
                "approved_seq": approved_seq,
                "detail": detail,
            },
        )
        await _send_result(hub, websocket, sender, request, False, detail, approved_seq)
        return
    bundle = merge_enrolled_keys(cast(EventSignatureTrustBundle, static), updated)
    hub._enrolled_identity_keys = updated
    hub.identity_trust_bundle = bundle
    hub._identity_gate.replace_trust_bundle(bundle)
    hub._enrollment_rate.record(sender, now=moment)
    rotated_socket = (
        hub.clients.revoke_name(request.name)
        if request.expected_key_id and request.name != sender
        else None
    )
    applied_seq = record_identity_enrollment(
        journal,
        {
            **provenance,
            "status": "applied",
            "applied": True,
            "approved_seq": approved_seq,
            "evicted_live_socket": rotated_socket is not None,
        },
    )
    logger.warning(
        "identity enrolment applied operator=%s name=%s key_id=%s rotated=%s audit_seq=%d",
        sender,
        request.name,
        request.key_id,
        request.expected_key_id or "-",
        applied_seq,
    )
    if rotated_socket is not None:
        await hub.clients.close_socket(
            rotated_socket, code=KEY_ROTATED_CLOSE_CODE, reason="identity key rotated"
        )
    detail = (
        f"identity key {request.key_id!r} enrolled for {request.name!r}"
        if not request.expected_key_id
        else f"identity key for {request.name!r} rotated to {request.key_id!r}"
    )
    await _send_result(hub, websocket, sender, request, True, detail, applied_seq)
    await hub._broadcast(
        hub._system(
            f"Identity key {request.key_id!r} for {request.name!r} was enrolled by "
            f"operator {sender!r}.",
            msg_type=MessageType.SYSTEM,
            event_kind=EventKind.IDENTITY_ENROLLMENT,
            operator=sender,
            name=request.name,
            key_id=request.key_id,
            previous_key_id=request.expected_key_id,
            audit_seq=applied_seq,
        )
    )


def _acl_allows(hub: SynapseHub, sender: str, name: str) -> bool:
    """Return whether the always-on enrolment grant authorises this exact name."""
    if hub.acl_policy is None:
        return False
    decision = evaluate_access(
        subject=sender,
        project=project_of(sender),
        permission=IDENTITY_ENROLL,
        target=Target("agent", name),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


async def _send_result(
    hub: SynapseHub,
    websocket: Any,
    sender: str,
    request: EnrollmentRequest,
    applied: bool,
    detail: str,
    audit_seq: int | None,
) -> None:
    """Send one private typed enrolment verdict to the requesting operator."""
    await hub._send_json(
        websocket,
        hub._system(
            detail,
            msg_type=MessageType.IDENTITY_ENROLL_RESULT,
            target=sender,
            name=request.name,
            key_id=request.key_id,
            expected_key_id=request.expected_key_id,
            applied=applied,
            audit_seq=audit_seq,
        ),
    )
