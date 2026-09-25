# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Read exact current custody without rewriting historical transaction outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState


@dataclass(frozen=True)
class ProtectedReservationLineage:
    """Internal authorized-reader view, never a write or revocation receipt."""

    reservations: tuple[ProtectedWriteReservation, ...]
    custody_holder: str | None

    @property
    def requested(self) -> ProtectedWriteReservation:
        """Return the originally queried transaction's unchanged historical value."""
        return self.reservations[0]

    @property
    def current(self) -> ProtectedWriteReservation:
        """Return the latest descendant; its outcome belongs only to that transaction."""
        return self.reservations[-1]


def read_protected_lineage(
    state: SynapseState, reservation_id: str, *, max_reservations: int
) -> ProtectedReservationLineage | None:
    """Resolve a bounded immutable custody view from a serialized state snapshot.

    Parameters
    ----------
    state:
        Trusted actor-consistent state snapshot, never concurrently mutable input.
    reservation_id:
        Exact authorized reservation identifier, not a broadcast or scope query.
    max_reservations:
        Positive enrolled history budget bounding the entire traversal.

    Returns
    -------
    ProtectedReservationLineage or None
        Requested historical value, descendants and actual custody holder.
        None means unknown, never cancelled, released or effectively revoked.

    Raises
    ------
    ValueError
        On an invalid budget or inconsistent/dangling/cyclic custody history.

    Notes
    -----
    The caller must authorize access before entry. This is an internal view, not
    a new wire schema. Descendant settlement never changes the parent's original
    outcome or creates an effective-revocation receipt for it. No execution,
    filesystem read, state mutation or repair is performed.
    """
    if type(max_reservations) is not int or max_reservations <= 0:
        raise ValueError("invalid lineage history budget")
    chain: list[ProtectedWriteReservation] = []
    seen: set[str] = set()
    current_id = reservation_id
    while True:
        if current_id in seen or len(chain) >= max_reservations:
            raise ValueError("cyclic or over-budget protected lineage")
        current = state.protected_write_reservations.get(current_id)
        if current is None:
            if not chain and current_id not in (
                state.protected_write_admissions.keys()
                | state.protected_claim_custody.keys()
                | state.protected_write_recoveries.keys()
            ):
                return None
            raise ValueError("dangling protected lineage")
        if (
            current.admission.reservation_id != current_id
            or state.protected_write_admissions.get(current_id) != current.admission
            or (chain and current.inherited_custody != chain[-1].custody)
        ):
            raise ValueError("protected lineage metadata or inherited custody mismatch")
        if chain:
            source = json.loads(current.admission.request_bytes)
            admitted = json.loads(current.admission.result_bytes)["body"]
            if (
                source["type"] != "protected_write_recover"
                or source["body"].get("parent_reservation_id") != chain[-1].admission.reservation_id
                or admitted["admission_sequence"] <= chain[-1].transition_sequence
            ):
                raise ValueError("protected lineage child does not bind its parent")
        chain.append(current)
        seen.add(current_id)
        child_id = state.protected_write_recoveries.get(current_id)
        held = state.protected_claim_custody.get(current_id)
        if child_id is None:
            expected = current.custody if current.holds_custody else None
            if held != expected:
                raise ValueError("protected lineage terminal custody mismatch")
            return ProtectedReservationLineage(
                tuple(chain), current_id if current.holds_custody else None
            )
        if held is not None or not current.holds_custody:
            raise ValueError("transferred parent has inconsistent custody")
        current_id = child_id
