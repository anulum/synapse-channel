# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lifecycle classification for imported federation peerings
"""Lifecycle classification for imported federation peerings.

Imported federation material is not a static trust blob. Operators need to see
three independent facts whenever they inspect a peering: whether it can currently
authorise, how close its bundle expiry is, and whether its key material is in a
rotation grace window. This module keeps that classification pure and shared so
CLI and dashboard surfaces do not grow their own slightly different lifecycle
rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.federation_store import FederationRecord
from synapse_channel.core.federation_wire import render_expiry

SECONDS_PER_DAY = 86400.0
"""One day of federation lifecycle time, in epoch seconds."""

FederationLifecycleState = Literal["active", "expired", "revoked"]
"""Operator-facing authorisation state of an imported peering."""

FederationRotationState = Literal["incomplete", "overlap", "steady"]
"""Operator-facing rotation state of the imported bundle's credential material."""


@dataclass(frozen=True)
class FederationLifecycle:
    """Derived lifecycle facts for one imported federation record.

    Attributes
    ----------
    state : {"active", "expired", "revoked"}
        Whether the peering currently authorises anything. Revocation wins over
        expiry because an operator explicitly took the peering out of service.
    rotation_state : {"incomplete", "overlap", "steady"}
        ``overlap`` means more than one signing key or certificate pin is
        accepted, the expected add-new-before-retire rotation window. ``incomplete``
        means one credential set is empty and the peering cannot authorise.
    expires_label : str
        Deterministic UTC expiry rendering, or ``never``.
    expires_in_days : float or None
        Signed days until expiry. Negative values mean the bundle is already
        expired; ``None`` means it has no expiry.
    expiry_note : str
        Short human-readable note matching ``expires_in_days``.
    imported_age_days : float
        Non-negative age of the operator-confirmed import ceremony.
    stale : bool
        ``True`` when an active peering's import age exceeds the optional
        operator-supplied freshness policy.
    """

    state: FederationLifecycleState
    rotation_state: FederationRotationState
    expires_label: str
    expires_in_days: float | None
    expiry_note: str
    imported_age_days: float
    stale: bool


def classify_federation_lifecycle(
    record: FederationRecord, *, now: float, max_age_days: float | None = None
) -> FederationLifecycle:
    """Return lifecycle facts for ``record`` at ``now``.

    Parameters
    ----------
    record : FederationRecord
        Imported peering and provenance from the operator's federation store.
    now : float
        POSIX timestamp used for expiry and import-age calculations.
    max_age_days : float or None, optional
        Optional operator freshness policy. When provided, only active peerings
        older than this many days are marked stale.
    """
    peer = record.peer
    if peer.revoked:
        state: FederationLifecycleState = "revoked"
    elif peer.expires_at is not None and now >= peer.expires_at:
        state = "expired"
    else:
        state = "active"

    if not peer.signing_key_ids or not peer.certificate_pins:
        rotation_state: FederationRotationState = "incomplete"
    elif len(peer.signing_key_ids) > 1 or len(peer.certificate_pins) > 1:
        rotation_state = "overlap"
    else:
        rotation_state = "steady"

    imported_age_days = max(0.0, now - record.provenance.imported_at) / SECONDS_PER_DAY
    expires_in_days = None if peer.expires_at is None else (peer.expires_at - now) / SECONDS_PER_DAY
    return FederationLifecycle(
        state=state,
        rotation_state=rotation_state,
        expires_label=render_expiry(peer.expires_at),
        expires_in_days=expires_in_days,
        expiry_note=_expiry_note(expires_in_days),
        imported_age_days=imported_age_days,
        stale=max_age_days is not None and state == "active" and imported_age_days > max_age_days,
    )


def _expiry_note(expires_in_days: float | None) -> str:
    """Return the compact lifecycle note for an expiry distance."""
    if expires_in_days is None:
        return "no expiry"
    if expires_in_days >= 0:
        return f"in {expires_in_days:.1f}d"
    return f"expired {-expires_in_days:.1f}d ago"
