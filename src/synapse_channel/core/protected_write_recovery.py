# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Bind recovery authority and retained parent evidence without transferring custody."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from synapse_channel.core.protected_write_admission import (
    ProtectedAdmissionContext,
    ProtectedWriteAdmission,
    _bind_protected_admission,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.state_models import TaskClaim


@dataclass(frozen=True)
class ProtectedRecoveryAdmission:
    """Validated parent/child pair; construction alone is not execution authority."""

    parent: ProtectedWriteReservation
    admission: ProtectedWriteAdmission


def bind_protected_recovery(
    parent: ProtectedWriteReservation,
    request: str | bytes,
    result: str | bytes,
    *,
    context: ProtectedAdmissionContext,
    claims: Mapping[str, TaskClaim],
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
    verify_recovery: Callable[[ProtectedWriteReservation, ProtectedWriteAdmission], bool],
) -> ProtectedRecoveryAdmission:
    """Bind a fresh service admission to the exact blocked parent.

    Parameters
    ----------
    parent:
        Immutable current parent from the authoritative actor candidate.
    request:
        Authenticated recovery request under separately authorized service credentials.
    result:
        Authority-signed child admission response with its actual journal sequence.
    context:
        Server-resolved recovery-service and writer identities and current time.
    claims:
        Current independently issued recovery claims, not renewed parent leases.
    limits:
        Explicit enrollment representation budgets.
    reason_codes:
        Enrolled result vocabulary.
    verify_recovery:
        Trusted local retained-evidence verifier. It must prove old-writer
        quiescence, recovery-policy authority, known observed before states,
        immutable content and every primary/auxiliary effect within the existing
        reserved domain. Unknown foreign bytes must be refused. Return exactly
        True, with no filesystem/network I/O or waiting under the actor lock.

    Returns
    -------
    ProtectedRecoveryAdmission
        Bound pair for atomic custody transfer, not an independently installed grant.

    Raises
    ------
    ValueError
        On a stale-domain, reused identity, invalid claim or missing recovery proof.

    Notes
    -----
    This performs no custody transfer or execution. The caller must CAS the exact
    parent and install the child in the same journal operation without releasing
    the domain between them. Ordinary admission installation must not be used.
    Authentication, active sessions and service enrollment precede this function.
    """
    admission = _bind_protected_admission(
        request,
        result,
        context=context,
        claims=claims,
        limits=limits,
        reason_codes=reason_codes,
        request_type="protected_write_recover",
    )
    source = json.loads(admission.request_bytes)
    origin = json.loads(parent.admission.request_bytes)
    old = json.loads(parent.result_bytes)["body"]
    body = json.loads(admission.result_bytes)["body"]
    proposal = json.loads(admission.proposal_bytes)
    original_proposal = json.loads(parent.admission.proposal_bytes)
    if (
        old["operation_phase"] != "recovery_required"
        or old["revocation_phase"] == "effective"
        or not parent.holds_custody
        or source["body"]["parent_reservation_id"] != parent.admission.reservation_id
        or admission.reservation_id == parent.admission.reservation_id
        or source["transaction_id"] == origin["transaction_id"]
        or (origin["type"] != "protected_write_recover" and source["sender"] == origin["sender"])
        or body["writer_incarnation"] == old["writer_incarnation"]
        or body["admission_sequence"] <= parent.transition_sequence
    ):
        raise ValueError("recovery requires a fresh identity and blocked parent")
    for field in ("authority_id", "authority_continuity", "enrollment_revision", "target"):
        if source[field] != origin[field]:
            raise ValueError("recovery changed the reserved authority domain")
    for field in ("target_project", "recovery_policy_revision"):
        if proposal[field] != original_proposal[field]:
            raise ValueError("recovery changed the parent project or recovery policy")
    parent_claim_ids = {witness.task_id for witness in parent.custody}
    if any(witness.task_id in parent_claim_ids for witness in admission.claim_custody):
        raise ValueError("recovery must not reuse the parent's author claims")
    if verify_recovery(parent, admission) is not True:
        raise ValueError("recovery requires verified quiescence and bounded known effects")
    return ProtectedRecoveryAdmission(parent, admission)
