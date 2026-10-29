# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact retained recovery admission input binding
"""Bind pure recovery inputs to an actor-current parent and validated child admission."""

from __future__ import annotations

import json

from synapse_channel.core.protected_write_admission import ProtectedWriteAdmission
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_recovery_candidate import ProtectedRecoveryCandidate


def verify_protected_recovery_admission_inputs(
    candidate: ProtectedRecoveryCandidate,
    *,
    parent: ProtectedWriteReservation,
    admission: ProtectedWriteAdmission,
) -> None:
    """Require the exact retained candidate at the current recovery admission boundary.

    Parameters
    ----------
    candidate:
        Server-retained result of pure known-state candidate preparation. Never
        construct this from an incoming request or untrusted serialized record.
    parent:
        Exact immutable current reservation read by the authority actor.
    admission:
        Child already bound to authenticated service, fresh claims, writer and
        journal sequence by the ordinary internal recovery admission checks.

    Raises
    ------
    ValueError
        On a stale parent, missing custody, wrong recovery phase, substituted
        proposal or another request/authority domain.

    Notes
    -----
    Performs no I/O. Use inside the trusted recovery verifier after admission
    representation/identity checks and before independent physical custody proof.
    A successful return proves this input binding only, never quiescence,
    descriptor nonescape, authority installation or a begin permit. The full
    recovery verifier must still independently prove and retain physical custody.
    """
    if candidate.parent != parent:
        raise ValueError("recovery candidate parent is no longer actor-current")
    body = json.loads(parent.result_bytes)["body"]
    # Keep local custody checking alongside the actor and state-transfer guards.
    if (
        not parent.holds_custody
        or body["operation_phase"] != "recovery_required"
        or body["revocation_phase"] == "effective"
    ):
        raise ValueError("recovery candidate requires current blocked custody")
    if admission.proposal_bytes != candidate.proposal_bytes:
        raise ValueError("recovery admission differs from retained candidate proposal")
    source = json.loads(admission.request_bytes)
    origin = json.loads(parent.admission.request_bytes)
    if (
        source["type"] != "protected_write_recover"
        or source["body"]["parent_reservation_id"] != parent.admission.reservation_id
        or any(
            source[field] != origin[field]
            for field in ("authority_id", "authority_continuity", "enrollment_revision", "target")
        )
    ):
        raise ValueError("recovery admission differs from retained candidate authority domain")
