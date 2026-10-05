# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — record an owner's redacted pool advertisement (F03 option A)
"""Handle ``entitlement_advert``: journal one redacted pool advertisement.

The owner builds the advertisement from the private ledger
(:mod:`synapse_channel.core.entitlement_advert`) and sends it here. The hub
records it only as an audit-only journal row, which fleet mirrors replicate. It
is never broadcast to connected seats, so a seat without journal access
cannot see it. The gates run before the payload is read:
1. a durable journal;
2. a cryptographically proven sender;
3. the always-enforced ``entitlement-advertise`` ACL grant for the alias.
Then the payload must have exactly the advertisement shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import ENTITLEMENT_ADVERTISE, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.entitlement_advert import EntitlementAdvertError, validate_advert
from synapse_channel.core.journal import record_entitlement_advert
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.hub")


async def handle_entitlement_advert(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Journal one advertisement after every gate passes; answer the sender privately."""
    raw = data.get("advert")
    alias = raw.get("pool_alias") if isinstance(raw, dict) else None
    alias_text = alias if isinstance(alias, str) else ""
    requester_pin = hub._identity_pins.pinned(sender)
    denial = ""
    if hub.journal is None:
        denial = "advertisements need a hub with a durable journal"
    elif not (hub.require_identity_binding or requester_pin is not None):
        denial = "advertisements require a cryptographically proven sender"
    elif not _acl_allows(hub, sender, alias_text):
        denial = "not authorised to advertise this pool alias"
    advert: dict[str, Any] | None = None
    if not denial:
        try:
            advert = validate_advert(raw)
        except EntitlementAdvertError as exc:
            denial = str(exc)
    if denial or advert is None or hub.journal is None:
        logger.warning(
            "entitlement advertisement refused sender=%s alias=%s detail=%s",
            sender,
            alias_text,
            denial,
        )
        await _send_result(hub, websocket, sender, alias_text, False, denial, None)
        return
    seq = record_entitlement_advert(
        hub.journal,
        {
            "advertiser": sender,
            "advertiser_key_id": requester_pin.key_id if requester_pin is not None else "bundle",
            "hub_id": hub.hub_id,
            "advert": advert,
        },
    )
    await _send_result(
        hub, websocket, sender, alias_text, True, f"advertisement recorded for {alias_text!r}", seq
    )


def _acl_allows(hub: SynapseHub, sender: str, alias: str) -> bool:
    """Return whether the always-on grant authorises advertising ``alias``."""
    if hub.acl_policy is None or not alias:
        return False
    decision = evaluate_access(
        subject=sender,
        project=project_of(sender),
        permission=ENTITLEMENT_ADVERTISE,
        target=Target("pool-alias", alias),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


async def _send_result(
    hub: SynapseHub,
    websocket: Any,
    sender: str,
    alias: str,
    applied: bool,
    detail: str,
    audit_seq: int | None,
) -> None:
    """Send the private verdict to the advertiser."""
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.ENTITLEMENT_ADVERT_RESULT,
            target=sender,
            pool_alias=alias,
            applied=applied,
            audit_seq=audit_seq,
        ),
    )
