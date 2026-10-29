# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — known-state recovery candidate preparation
"""Check a recovery proposal against retained observations without granting authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from synapse_channel.core.protected_write_content import (
    ProtectedWriteContent,
    bind_protected_write_content,
)
from synapse_channel.core.protected_write_effects import PathKey, verify_protected_effects
from synapse_channel.core.protected_write_inspection import (
    ProtectedFileInspection,
    verify_protected_auxiliary_before_states,
    verify_protected_parent_bindings,
)
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import (
    PreparedProtectedWrite,
    ProtectedPreparationPolicy,
)
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_recovery_evidence import FrozenProtectedRecoveryEvidence


@dataclass(frozen=True)
class ProtectedRecoveryCandidate:
    """Purely checked recovery inputs, never admission, custody transfer or begin."""

    parent: ProtectedWriteReservation
    proposal_bytes: bytes
    content: ProtectedWriteContent
    observations: tuple[tuple[PathKey, ProtectedFileInspection | None], ...]
    deferred_parents: tuple[tuple[PathKey, PathKey], ...]


def _verify_recovery_parent(
    parent: ProtectedWriteReservation,
    original: PreparedProtectedWrite,
    evidence: FrozenProtectedRecoveryEvidence,
    policy: ProtectedPreparationPolicy,
) -> None:
    """Bind immutable recovery evidence to the retained parent and enrolled domain."""
    frozen = json.loads(evidence.content)
    if (
        parent.admission != original.admission
        or not parent.holds_custody
        or frozen["reservation_id"] != parent.admission.reservation_id
        or frozen["admission_request_sha256"]
        != hashlib.sha256(parent.admission.request_bytes).hexdigest()
        or policy.enrollment_revision != original.policy.enrollment_revision
        or policy.limits != original.policy.limits
        or dict(policy.enrolled_roots) != dict(original.policy.enrolled_roots)
    ):
        raise ValueError("recovery candidate parent or enrollment mismatch")


def prepare_protected_recovery_candidate(
    proposal: str | bytes,
    *,
    parent: ProtectedWriteReservation,
    original: PreparedProtectedWrite,
    evidence: FrozenProtectedRecoveryEvidence,
    policy: ProtectedPreparationPolicy,
    artifacts: Mapping[PathKey, bytes],
) -> ProtectedRecoveryCandidate:
    """Require known before states, exact effect enrollment and immutable content.

    Parameters
    ----------
    proposal:
        Complete recovery proposal with independently issued fresh claim IDs.
    parent:
        Server-retained immutable reservation snapshot for subsequent actor CAS.
    original:
        Trusted original prepared inputs, independent of the proposed recovery.
    evidence:
        Server-retained result of bounded immutable recovery evidence prefetch.
        Never a caller-deserialised object or a client-provided observation map.
    policy:
        Independently operator-enrolled recovery effect/content/claim policy.
        Roots, limits and revision must preserve the parent's reserved domain.
    artifacts:
        Already prefetched immutable candidate bytes in enrolled content domains.

    Returns
    -------
    ProtectedRecoveryCandidate
        Bound pure inputs for a separate authority/physical recovery verifier.

    Raises
    ------
    ValueError
        On wrong parent/domain, reused claim, unknown path/state, missing parent
        provenance, unpermitted effect or changed/unverified artifact bytes.

    Notes
    -----
    Performs no filesystem, journal or network I/O. The actor must compare the
    exact retained parent with current custody, require recovery_required phase,
    independently validate fresh claims/service/writer identity, and prove complete
    old-writer quiescence/custody before admitting or executing this candidate.
    Known observations cover every candidate path: new names require additional
    independently collected evidence and cannot inherit permission from a prefix.
    No admission is inferred from a digest, claim declaration or this object.
    """
    _verify_recovery_parent(parent, original, evidence, policy)
    parsed = parse_protected_write_proposal(proposal, limits=policy.limits)
    source = json.loads(parsed.canonical_bytes)
    predecessor = json.loads(original.admission.proposal_bytes)
    if any(
        source[field] != predecessor[field]
        for field in ("target_project", "recovery_policy_revision")
    ):
        raise ValueError("recovery candidate changed reserved project or policy")
    if set(parsed.claim_ids) & {claim.task_id for claim in parent.custody}:
        raise ValueError("recovery candidate reused parent claim custody")
    known = dict(evidence.observations)
    paths = {
        (path["root_id"], path["relative_path"])
        for operation in source["auxiliary_operations"]
        for path in operation["paths"]
    }
    if not paths <= known.keys():
        raise ValueError("recovery candidate path lacks known observation")
    inspections = {key: observed for key, observed in known.items() if observed is not None}
    verify_protected_effects(
        parsed.canonical_bytes,
        limits=policy.limits,
        primary_claims=policy.primary_claims,
        auxiliary_opcodes=policy.auxiliary_opcodes,
        enrolled_parents=policy.enrolled_parents,
    )
    verified = verify_protected_auxiliary_before_states(
        parsed.canonical_bytes,
        limits=policy.limits,
        inspections=inspections,
        enrolled_roots=policy.enrolled_roots,
    )
    deferred = verify_protected_parent_bindings(
        parsed.canonical_bytes,
        limits=policy.limits,
        inspections=inspections,
        enrolled_roots=policy.enrolled_roots,
        enrolled_parents=policy.enrolled_parents,
    )
    content = bind_protected_write_content(
        parsed.canonical_bytes,
        limits=policy.limits,
        artifacts=artifacts,
        domain_verifiers=policy.domain_verifiers,
        max_artifact_bytes=policy.max_artifact_bytes,
    )
    return ProtectedRecoveryCandidate(
        parent,
        parsed.canonical_bytes,
        content,
        tuple((key, known[key]) for key in verified),
        deferred,
    )
