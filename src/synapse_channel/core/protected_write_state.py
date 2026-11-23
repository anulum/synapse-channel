# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the protected-write tables one hub keeps
"""The protected-write tables, separate from the lease and task core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from synapse_channel.core.protected_write_admission import ProtectedWriteAdmission
from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_recovery import ProtectedRecoveryAdmission

if TYPE_CHECKING:
    from synapse_channel.core.path_identity import ClaimScopeIdentity
    from synapse_channel.core.protected_write_enrollment_journal import ProtectedEnrollmentRecord
    from synapse_channel.core.state_models import TaskClaim


class ProtectedWriteState:
    """Own the seven protected-write tables and the operations that write them."""

    def __init__(self) -> None:
        self.protected_claim_custody: dict[str, tuple[ProtectedClaimCustody, ...]] = {}
        self.protected_write_admissions: dict[str, ProtectedWriteAdmission] = {}
        self.protected_write_reservations: dict[str, ProtectedWriteReservation] = {}
        self.protected_write_recoveries: dict[str, str] = {}
        self.protected_enrollments: dict[str, ProtectedEnrollmentRecord] = {}
        self.protected_enrollment_key_ids: set[str] = set()
        self.protected_enrollment_fingerprints: set[str] = set()

    def publish_from(self, candidate: ProtectedWriteState) -> None:
        """Install the candidate's tables by reference."""
        self.protected_claim_custody = candidate.protected_claim_custody
        self.protected_write_admissions = candidate.protected_write_admissions
        self.protected_write_reservations = candidate.protected_write_reservations
        self.protected_write_recoveries = candidate.protected_write_recoveries
        self.protected_enrollments = candidate.protected_enrollments
        self.protected_enrollment_key_ids = candidate.protected_enrollment_key_ids
        self.protected_enrollment_fingerprints = candidate.protected_enrollment_fingerprints

    def install_admission(
        self,
        admission: ProtectedWriteAdmission,
        *,
        max_reservations: int,
        claim_of: Callable[[str], TaskClaim | None],
    ) -> None:
        """Install trusted admission evidence and its custody in a private candidate."""
        if type(max_reservations) is not int or max_reservations <= 0:
            raise ValueError("invalid protected reservation budget")
        reservation_id = admission.reservation_id
        if (
            reservation_id in self.protected_write_admissions
            or reservation_id in self.protected_claim_custody
            or reservation_id in self.protected_write_reservations
        ):
            raise ValueError("protected reservation identity already exists")
        retained_ids = (
            self.protected_write_admissions.keys()
            | self.protected_claim_custody.keys()
            | self.protected_write_reservations.keys()
        )
        if len(retained_ids) >= max_reservations:
            raise ValueError("protected reservation budget exhausted")
        if not admission.claim_custody:
            raise ValueError("protected admission requires claim custody")
        for witness in admission.claim_custody:
            claim = claim_of(witness.task_id)
            if claim is None or ProtectedClaimCustody.capture(claim) != witness:
                raise ValueError("protected admission witness changed before installation")
            if self.scope_held(
                witness.task_id, witness.worktree, witness.paths, witness.path_identity
            ):
                raise ValueError("protected admission overlaps outstanding custody")
        self.protected_write_admissions[reservation_id] = admission
        self.protected_claim_custody[reservation_id] = admission.claim_custody
        self.protected_write_reservations[reservation_id] = ProtectedWriteReservation.admitted(
            admission
        )

    def transfer_custody(
        self,
        recovery: ProtectedRecoveryAdmission,
        *,
        max_reservations: int,
        claim_of: Callable[[str], TaskClaim | None],
    ) -> None:
        """Install a verified child and transfer custody in a private candidate."""
        parent, admission = recovery.parent, recovery.admission
        parent_id, child_id = parent.admission.reservation_id, admission.reservation_id
        if (
            self.protected_write_reservations.get(parent_id) != parent
            or self.protected_write_admissions.get(parent_id) != parent.admission
            or parent_id in self.protected_write_recoveries
            or not parent.holds_custody
            or self.protected_claim_custody.get(parent_id) != parent.custody
        ):
            raise ValueError("recovery parent or custody changed before transfer")
        if type(max_reservations) is not int or max_reservations <= 0:
            raise ValueError("invalid protected reservation budget")
        retained = (
            self.protected_write_admissions.keys()
            | self.protected_write_reservations.keys()
            | self.protected_claim_custody.keys()
        )
        if child_id in retained or len(retained) >= max_reservations:
            raise ValueError("recovery child identity or reservation budget unavailable")
        if not admission.claim_custody:
            raise ValueError("recovery requires fresh service custody")
        for witness in admission.claim_custody:
            claim = claim_of(witness.task_id)
            if claim is None or ProtectedClaimCustody.capture(claim) != witness:
                raise ValueError("recovery witness changed before transfer")
        child = replace(
            ProtectedWriteReservation.admitted(admission), inherited_custody=parent.custody
        )
        for other_id, witnesses in self.protected_claim_custody.items():
            if other_id != parent_id and any(
                held.conflicts(item.task_id, item.worktree, item.paths, item.path_identity)
                for held in witnesses
                for item in child.custody
            ):
                raise ValueError("recovery overlaps another reservation domain")
        self.protected_write_admissions[child_id] = admission
        self.protected_write_reservations[child_id] = child
        self.protected_claim_custody[child_id] = child.custody
        self.protected_write_recoveries[parent_id] = child_id
        del self.protected_claim_custody[parent_id]

    def apply_transition(
        self, previous: ProtectedWriteReservation, updated: ProtectedWriteReservation
    ) -> None:
        """Compare-and-swap a trusted transition in the private actor candidate."""
        reservation_id = previous.admission.reservation_id
        if (
            self.protected_write_reservations.get(reservation_id) != previous
            or reservation_id in self.protected_write_recoveries
            or self.protected_write_admissions.get(reservation_id) != previous.admission
            or updated.admission != previous.admission
            or updated.inherited_custody != previous.inherited_custody
            or updated.transition_sequence <= previous.transition_sequence
        ):
            raise ValueError("protected transition has a stale or changed predecessor")
        expected = previous.custody if previous.holds_custody else None
        if self.protected_claim_custody.get(reservation_id) != expected:
            raise ValueError("protected transition custody does not match its predecessor")
        if updated.holds_custody and not previous.holds_custody:
            raise ValueError("settled custody cannot be resurrected")
        self.protected_write_reservations[reservation_id] = updated
        if not updated.holds_custody:
            self.protected_claim_custody.pop(reservation_id, None)

    def task_held(self, task: str) -> bool:
        """Return whether a reservation retains the exact claim witness."""
        return any(
            witness.task_id == task
            for witnesses in self.protected_claim_custody.values()
            for witness in witnesses
        )

    def scope_held(
        self,
        task: str,
        worktree: str,
        paths: tuple[str, ...],
        path_identity: ClaimScopeIdentity | None,
    ) -> bool:
        """Compare retained scopes without author or lease-expiry exemptions."""
        return any(
            witness.conflicts(task, worktree, paths, path_identity)
            for witnesses in self.protected_claim_custody.values()
            for witness in witnesses
        )

    def record_enrollment(self, record: ProtectedEnrollmentRecord) -> None:
        """Store one enrollment and its key identity."""
        self.protected_enrollments[record.session_id] = record
        self.protected_enrollment_key_ids.add(record.key_id)
        self.protected_enrollment_fingerprints.add(record.key_fingerprint)

    def revoke_enrollment(self, session_id: str) -> None:
        """Mark one enrollment revoked and keep the record."""
        previous = self.protected_enrollments[session_id]
        self.protected_enrollments[session_id] = replace(previous, revoked=True)
