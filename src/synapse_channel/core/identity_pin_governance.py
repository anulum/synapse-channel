# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed policy for governed identity-pin recovery
"""Policy for reclaiming a stale trust-on-first-use identity pin.

Removing a pin is intentionally more privileged than claiming a free name: it
undoes a durable key binding. The policy therefore composes independent gates
before the store is touched: an ACL grant, a cryptographically bound requester,
an append-only audit journal, an exact expected-key comparison, and the name's
live ownership state. Normal recovery waits until the holder is offline and its
ownership lease has lapsed under the configured offline TTL, or until a socket-up
holder has lacked both a reaction and a live waiter for that same TTL after its
reaction window elapsed. Any other live or still-leased holder requires explicit
break-glass; there is no silent key rotation or automatic takeover in this module.
"""

from __future__ import annotations

from synapse_channel.core.identity_pins import IdentityPin

MAX_PIN_RECLAIM_REASON_LENGTH = 500
"""Longest operator reason admitted to the durable audit event."""


def pin_reclaim_denial(
    *,
    requester: str,
    pin_name: str,
    expected_key_id: str,
    reason: str,
    pin: IdentityPin | None,
    acl_allowed: bool,
    requester_bound: bool,
    journal_available: bool,
    owner_online: bool,
    lease_live: bool,
    stale_owner_reclaimable: bool,
    break_glass: bool,
) -> str:
    """Return the first fail-closed denial, or ``""`` when reclaim may run.

    The ordering avoids disclosing whether a target is pinned, online, or
    leased until the requester has both the ACL grant and a cryptographically
    proven connection identity. All inputs are observations made immediately
    before the synchronous compare-and-swap in :class:`IdentityPinStore`.
    """
    clean_name = pin_name.strip()
    clean_key = expected_key_id.strip()
    clean_reason = reason.strip()
    if not clean_name:
        return "pin name is required"
    if not clean_key:
        return "expected key id is required"
    if not clean_reason:
        return "a non-empty operator reason is required"
    if len(clean_reason) > MAX_PIN_RECLAIM_REASON_LENGTH:
        return f"operator reason exceeds {MAX_PIN_RECLAIM_REASON_LENGTH} characters"
    if requester == clean_name:
        return "an operator cannot reclaim the identity used by its own live connection"
    if not acl_allowed:
        return "no identity-pin-reclaim ACL grant authorises this operator and target"
    if not requester_bound:
        return "the operator identity is not cryptographically bound"
    if not journal_available:
        return "the hub has no durable journal for the mandatory reclaim audit event"
    if pin is None:
        return "the target has no identity pin"
    if pin.key_id != clean_key:
        return "the current pin does not match the expected key id"
    stale_online_override = owner_online and stale_owner_reclaimable
    if not break_glass and owner_online and not stale_online_override:
        return "the pinned identity is online; use explicit break-glass recovery to evict it"
    if not break_glass and lease_live and not stale_online_override:
        return "the ownership lease is still live; wait for its offline TTL or use break-glass"
    return ""
