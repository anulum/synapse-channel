# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — handle the governed identity-key enrolment verbs (SOL4-ID-01)
"""Handle ``identity_enroll`` and ``identity_revoke`` under a proven operator.

The order of gates and the pure policy live in
:mod:`synapse_channel.core.identity_enrollments`. These handlers collect the
observations the policy needs from the hub, write the durable audit before the
store changes, persist the hub-owned enrolment store, swap the effective trust
bundle into the identity gate, and answer the operator privately.

- An enrolled key is usable at the next registration, with no restart.
- Rotating or revoking another name's key detaches that name's live socket,
  which proved the superseded key. An operator acting on its own key stays
  connected.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.acl import IDENTITY_ENROLL, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.identity_enrollments import (
    ENROLLER_ROLE,
    EnrollmentRequest,
    authority_denial,
    decode_public_key,
    enrollment_denial,
    merge_enrolled_keys,
    revocation_denial,
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
"""Close code sent to a live socket whose identity key an operator rotated or revoked."""


@dataclass(frozen=True)
class _Authority:
    """What the hub observed about a requester before any request field is read."""

    enabled: bool
    requester_bound: bool
    acl_allowed: bool
    role_granted: bool
    namespace_allowed: bool
    rate_allowed: bool
    operator_key_id: str
    moment: float

    @property
    def authorised(self) -> bool:
        """Whether the requester passed every gate that proves who it is."""
        return self.enabled and self.requester_bound and self.acl_allowed and self.role_granted

    def denial(self) -> str:
        """Return the first failed authority gate, or ``""``."""
        return authority_denial(
            enabled=self.enabled,
            requester_bound=self.requester_bound,
            acl_allowed=self.acl_allowed,
            role_granted=self.role_granted,
            namespace_allowed=self.namespace_allowed,
            rate_allowed=self.rate_allowed,
        )


def _authority(hub: SynapseHub, sender: str, name: str) -> _Authority:
    requester_pin = hub._identity_pins.pinned(sender)
    moment = hub._clock()
    return _Authority(
        enabled=(
            hub.identity_enrollment_path is not None
            and hub.journal is not None
            and hub._static_identity_trust is not None
        ),
        requester_bound=hub.require_identity_binding or requester_pin is not None,
        acl_allowed=_acl_allows(hub, sender, name),
        role_granted=hub.role_grants is not None
        and hub.role_grants.may_claim(sender, f"{project_of(sender)}/{ENROLLER_ROLE}"),
        namespace_allowed=project_of(name) in hub.identity_enrollment_namespaces,
        rate_allowed=hub._enrollment_rate.allows(sender, now=moment),
        operator_key_id=requester_pin.key_id if requester_pin is not None else "bundle",
        moment=moment,
    )


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
    authority = _authority(hub, sender, request.name)
    static = hub._static_identity_trust
    denial = enrollment_denial(
        request,
        enabled=authority.enabled,
        requester_bound=authority.requester_bound,
        acl_allowed=authority.acl_allowed,
        role_granted=authority.role_granted,
        namespace_allowed=authority.namespace_allowed,
        rate_allowed=authority.rate_allowed,
        static=static.keys if static is not None else {},
        enrolled=hub._enrolled_identity_keys,
        now=time.time(),
    )
    provenance: dict[str, Any] = {
        "action": "rotate" if request.expected_key_id else "enroll",
        "operator": sender,
        "operator_key_id": authority.operator_key_id,
        "name": request.name,
        "key_id": request.key_id,
        "public_key_sha256": (
            hashlib.sha256(request.public_key).hexdigest() if request.public_key else ""
        ),
        "expected_key_id": request.expected_key_id,
        "reason": request.reason.strip(),
    }
    result = _Result(MessageType.IDENTITY_ENROLL_RESULT, request.name, request.key_id)
    if denial:
        await _refuse(hub, websocket, sender, authority, provenance, result, denial)
        return
    updated = dict(hub._enrolled_identity_keys)
    if request.expected_key_id:
        updated[request.expected_key_id] = replace(updated[request.expected_key_id], revoked=True)
    updated[request.key_id] = EventSignatureKey(
        key_id=request.key_id,
        public_key=cast(bytes, request.public_key),  # validated by the policy
        senders=frozenset({request.name}),
        expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
    )
    detail = (
        f"identity key for {request.name!r} rotated to {request.key_id!r}"
        if request.expected_key_id
        else f"identity key {request.key_id!r} enrolled for {request.name!r}"
    )
    await _apply(
        hub,
        websocket,
        sender,
        authority,
        provenance,
        result,
        updated,
        evict=bool(request.expected_key_id),
        detail=detail,
    )


async def handle_identity_revoke(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Revoke one enrolled identity key, without a replacement, after every gate passes."""
    name = str(data.get("name") or "").strip()
    key_id = str(data.get("key_id") or "").strip()
    reason = str(data.get("reason") or "")
    authority = _authority(hub, sender, name)
    static = hub._static_identity_trust
    denial = authority.denial() or revocation_denial(
        name=name,
        key_id=key_id,
        reason=reason,
        static=static.keys if static is not None else {},
        enrolled=hub._enrolled_identity_keys,
    )
    provenance: dict[str, Any] = {
        "action": "revoke",
        "operator": sender,
        "operator_key_id": authority.operator_key_id,
        "name": name,
        "key_id": key_id,
        "reason": reason.strip(),
    }
    result = _Result(MessageType.IDENTITY_REVOKE_RESULT, name, key_id)
    if denial:
        await _refuse(hub, websocket, sender, authority, provenance, result, denial)
        return
    updated = dict(hub._enrolled_identity_keys)
    updated[key_id] = replace(updated[key_id], revoked=True)
    await _apply(
        hub,
        websocket,
        sender,
        authority,
        provenance,
        result,
        updated,
        evict=True,
        detail=f"identity key {key_id!r} for {name!r} revoked",
    )


@dataclass(frozen=True)
class _Result:
    """The private verdict frame a request is answered with."""

    msg_type: str
    name: str
    key_id: str


async def _refuse(
    hub: SynapseHub,
    websocket: Any,
    sender: str,
    authority: _Authority,
    provenance: dict[str, Any],
    result: _Result,
    denial: str,
) -> None:
    logger.warning(
        "identity %s denied operator=%s name=%s key_id=%s detail=%s",
        provenance["action"],
        sender,
        result.name,
        result.key_id,
        denial,
    )
    audit_seq = None
    if authority.authorised:
        # Only an authorised operator's refusals are journalled, so an unproven
        # socket cannot fill the durable audit trail.
        audit_seq = record_identity_enrollment(
            cast(EventStore, hub.journal),
            {**provenance, "status": "denied", "applied": False, "detail": denial},
        )
    await _send_result(hub, websocket, sender, result, False, denial, audit_seq)


async def _apply(
    hub: SynapseHub,
    websocket: Any,
    sender: str,
    authority: _Authority,
    provenance: dict[str, Any],
    result: _Result,
    updated: dict[str, EventSignatureKey],
    *,
    evict: bool,
    detail: str,
) -> None:
    """Apply an approved change, keeping storage diagnostics out of verdicts and audits."""
    journal = cast(EventStore, hub.journal)  # availability is part of the authority
    approved_seq = record_identity_enrollment(
        journal, {**provenance, "status": "approved", "applied": False}
    )
    try:
        write_enrolled_keys(cast(Path, hub.identity_enrollment_path), updated)
    except OSError:
        logger.exception("Identity enrolment persistence failed")
        failure = "could not persist the enrolment store"
        record_identity_enrollment(
            journal,
            {
                **provenance,
                "status": "not_applied",
                "applied": False,
                "approved_seq": approved_seq,
                "detail": failure,
            },
        )
        await _send_result(hub, websocket, sender, result, False, failure, approved_seq)
        return
    bundle = merge_enrolled_keys(
        cast(EventSignatureTrustBundle, hub._static_identity_trust), updated
    )
    hub._enrolled_identity_keys = updated
    hub.identity_trust_bundle = bundle
    hub._identity_gate.replace_trust_bundle(bundle)
    hub._enrollment_rate.record(sender, now=authority.moment)
    evicted = hub.clients.revoke_name(result.name) if evict and result.name != sender else None
    applied_seq = record_identity_enrollment(
        journal,
        {
            **provenance,
            "status": "applied",
            "applied": True,
            "approved_seq": approved_seq,
            "evicted_live_socket": evicted is not None,
        },
    )
    logger.warning(
        "identity %s applied operator=%s name=%s key_id=%s audit_seq=%d",
        provenance["action"],
        sender,
        result.name,
        result.key_id,
        applied_seq,
    )
    if evicted is not None:
        await hub.clients.close_socket(
            evicted, code=KEY_ROTATED_CLOSE_CODE, reason="identity key rotated or revoked"
        )
    await _send_result(hub, websocket, sender, result, True, detail, applied_seq)
    await hub.broadcast(
        hub.system(
            f"Identity key change ({provenance['action']}) for {result.name!r} by operator "
            f"{sender!r}.",
            msg_type=MessageType.SYSTEM,
            event_kind=EventKind.IDENTITY_ENROLLMENT,
            action=provenance["action"],
            operator=sender,
            name=result.name,
            key_id=result.key_id,
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
    result: _Result,
    applied: bool,
    detail: str,
    audit_seq: int | None,
) -> None:
    """Send one private typed verdict to the requesting operator."""
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=result.msg_type,
            target=sender,
            name=result.name,
            key_id=result.key_id,
            applied=applied,
            audit_seq=audit_seq,
        ),
    )
