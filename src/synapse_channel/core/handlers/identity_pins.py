# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governed live-hub recovery for stale identity pins
"""Handle the operator-only identity-pin reclaim verb.

The generic ACL frame gate protects this mutation when global ACL enforcement
is enabled. This handler repeats the exact grant check unconditionally so an
open compatibility posture cannot turn pin removal into a public verb. Policy
and mutation stay outside ``hub.py``: the hub supplies its existing pin store,
client registry, ACL, journal, and transport primitives only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.acl import PIN_RECLAIM, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.identity_pin_governance import pin_reclaim_denial
from synapse_channel.core.journal import EventKind, record_identity_pin_reclaim
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.hub")

PIN_RECLAIM_CLOSE_CODE = 4017
"""Close code sent to a live holder evicted by an audited break-glass reclaim."""


async def handle_identity_pin_reclaim(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Reclaim one exact pin after every governance gate passes.

    A successful operation is write-ahead audited, compare-and-swap removes the
    observed key, revokes any break-glass live binding, records the applied
    phase, broadcasts an operator-visible notice, and returns a typed result.
    A denial changes nothing and returns the first actionable reason.
    """
    pin_name = str(data.get("pin_name") or "").strip()
    expected_key_id = str(data.get("expected_key_id") or "").strip()
    reason = str(data.get("reason") or "").strip()
    break_glass = data.get("break_glass") is True
    pin = hub._identity_pins.pinned(pin_name)
    owner_socket = hub.clients.agent_sockets.get(pin_name)
    owner_online = owner_socket is not None
    lease_live = hub.clients.ownership.is_leased(pin_name)
    requester_pin = hub._identity_pins.pinned(sender)
    requester_bound = hub.require_identity_binding or requester_pin is not None
    journal = hub.journal
    denial = pin_reclaim_denial(
        requester=sender,
        pin_name=pin_name,
        expected_key_id=expected_key_id,
        reason=reason,
        pin=pin,
        acl_allowed=_acl_allows(hub, sender, pin_name),
        requester_bound=requester_bound,
        journal_available=journal is not None,
        owner_online=owner_online,
        lease_live=lease_live,
        break_glass=break_glass,
    )
    if denial:
        logger.warning(
            "identity pin reclaim denied operator=%s pin_name=%s break_glass=%s detail=%s",
            sender,
            pin_name,
            break_glass,
            denial,
        )
        await _send_result(
            hub,
            websocket,
            sender,
            pin_name=pin_name,
            expected_key_id=expected_key_id,
            applied=False,
            break_glass=break_glass,
            detail=denial,
        )
        return

    journal = cast(EventStore, journal)  # availability is enforced by pin_reclaim_denial
    provenance: dict[str, Any] = {
        "operator": sender,
        "operator_key_id": requester_pin.key_id if requester_pin is not None else "bundle",
        "pin_name": pin_name,
        "expected_key_id": expected_key_id,
        "reason": reason,
        "break_glass": break_glass,
        "owner_online": owner_online,
        "lease_live": lease_live,
    }
    approved_seq = record_identity_pin_reclaim(
        journal, {**provenance, "status": "approved", "applied": False}
    )
    try:
        removed = hub._identity_pins.reclaim(pin_name, expected_key_id=expected_key_id)
    except OSError as exc:
        detail = f"could not persist the reclaimed pin table: {exc}"
        record_identity_pin_reclaim(
            journal,
            {
                **provenance,
                "status": "not_applied",
                "applied": False,
                "approved_seq": approved_seq,
                "detail": detail,
            },
        )
        await _send_result(
            hub,
            websocket,
            sender,
            pin_name=pin_name,
            expected_key_id=expected_key_id,
            applied=False,
            break_glass=break_glass,
            detail=detail,
            audit_seq=approved_seq,
        )
        return
    if removed is None:
        detail = "the identity pin changed before the reclaim could be applied"
        record_identity_pin_reclaim(
            journal,
            {
                **provenance,
                "status": "not_applied",
                "applied": False,
                "approved_seq": approved_seq,
                "detail": detail,
            },
        )
        await _send_result(
            hub,
            websocket,
            sender,
            pin_name=pin_name,
            expected_key_id=expected_key_id,
            applied=False,
            break_glass=break_glass,
            detail=detail,
            audit_seq=approved_seq,
        )
        return

    revoked_socket = hub.clients.revoke_name(pin_name)
    applied_seq = record_identity_pin_reclaim(
        journal,
        {
            **provenance,
            "status": "applied",
            "applied": True,
            "approved_seq": approved_seq,
            "evicted_live_socket": revoked_socket is not None,
        },
    )
    logger.warning(
        "identity pin reclaim applied operator=%s pin_name=%s key_id=%s "
        "break_glass=%s audit_seq=%d reason=%r",
        sender,
        pin_name,
        removed.key_id,
        break_glass,
        applied_seq,
        reason,
    )
    if revoked_socket is not None:
        await hub.clients.close_socket(
            revoked_socket,
            code=PIN_RECLAIM_CLOSE_CODE,
            reason="identity pin reclaimed",
        )
    await hub._broadcast(
        hub._system(
            f"Identity pin for {pin_name!r} was reclaimed by operator {sender!r}.",
            msg_type=MessageType.SYSTEM,
            event_kind=EventKind.IDENTITY_PIN_RECLAIM,
            operator=sender,
            pin_name=pin_name,
            previous_key_id=removed.key_id,
            break_glass=break_glass,
            audit_seq=applied_seq,
        )
    )
    await _send_result(
        hub,
        websocket,
        sender,
        pin_name=pin_name,
        expected_key_id=expected_key_id,
        applied=True,
        break_glass=break_glass,
        detail="identity pin reclaimed; the next valid proof may pin the name",
        audit_seq=applied_seq,
    )


def _acl_allows(hub: SynapseHub, sender: str, pin_name: str) -> bool:
    """Return whether the always-on reclaim grant authorises this exact target."""
    if hub.acl_policy is None:
        return False
    decision = evaluate_access(
        subject=sender,
        project=project_of(sender),
        permission=PIN_RECLAIM,
        target=Target("agent", pin_name),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


async def _send_result(
    hub: SynapseHub,
    websocket: Any,
    sender: str,
    *,
    pin_name: str,
    expected_key_id: str,
    applied: bool,
    break_glass: bool,
    detail: str,
    audit_seq: int | None = None,
) -> None:
    """Send one private typed reclaim verdict to the requesting operator."""
    await hub._send_json(
        websocket,
        hub._system(
            detail,
            msg_type=MessageType.IDENTITY_PIN_RECLAIM_RESULT,
            target=sender,
            pin_name=pin_name,
            expected_key_id=expected_key_id,
            applied=applied,
            break_glass=break_glass,
            audit_seq=audit_seq,
        ),
    )
