# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
"""Compose mandatory protected-writer input checks without issuing a begin permit."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_admission import ProtectedWriteAdmission
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
from synapse_channel.core.protected_write_journal import protected_operation_response
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import (
    ProtectedWriteProposalLimits,
    parse_protected_write_proposal,
)
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState


@dataclass(frozen=True)
class ProtectedPreparationPolicy:
    """Pinned server enrollment, with copied read-only maps and immutable values."""

    enrollment_revision: str
    limits: ProtectedWriteProposalLimits
    enrolled_roots: Mapping[str, tuple[int, int]]
    primary_claims: Mapping[PathKey, frozenset[str]]
    auxiliary_opcodes: Mapping[PathKey, frozenset[str]]
    enrolled_parents: Mapping[PathKey, PathKey]
    domain_verifiers: Mapping[str, Callable[[bytes, str], bool]]
    max_artifact_bytes: int

    def __post_init__(self) -> None:
        """Detach policy maps from mutable caller-owned dictionaries."""
        for name in (
            "enrolled_roots",
            "primary_claims",
            "auxiliary_opcodes",
            "enrolled_parents",
            "domain_verifiers",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True)
class PreparedProtectedWrite:
    """Validated immutable inputs, not a write permit or proof of current admission."""

    admission: ProtectedWriteAdmission
    policy: ProtectedPreparationPolicy
    content: ProtectedWriteContent
    observations: tuple[tuple[PathKey, ProtectedFileInspection | None], ...]
    deferred_parents: tuple[tuple[PathKey, PathKey], ...]


def prepare_protected_writer_input(
    admission: ProtectedWriteAdmission,
    proposal: str | bytes,
    *,
    policy: ProtectedPreparationPolicy,
    authenticated_writer: tuple[str, str],
    artifacts: Mapping[PathKey, bytes],
    inspections: Mapping[PathKey, ProtectedFileInspection],
) -> PreparedProtectedWrite:
    """Require matching admission, enrollment, writer, content, effects and before states.

    Parameters
    ----------
    admission:
        Trusted immutable authority admission, not a client-constructed object.
    proposal:
        Original proposal wire text; canonical bytes must match the admission.
    policy:
        Pinned operator enrollment with immutable permission/root/domain values.
    authenticated_writer:
        Server-resolved principal/incarnation pair from authenticated service context.
    artifacts:
        Immutable local artifact snapshot; no remote fetching.
    inspections:
        Trusted actor-consistent descriptor observation snapshot, gathered outside
        the authority lock. None in output denotes absence proven by prior mkdir.

    Returns
    -------
    PreparedProtectedWrite
        All required preparation checks passed for the exact admitted proposal.

    Raises
    ------
    ValueError
        If any mandatory binding, permission, content, before-state or budget fails.

    Notes
    -----
    The strict parser proves that primary initial/final states equal their
    auxiliary execution chain. Thus checking every initial auxiliary path also
    covers primary states, including proven children of newly planned directories.
    A separate durable begin against current custody/trust and descriptor
    revalidation at execution are still mandatory. This API never writes, enrolls
    a root, changes state, authenticates a claimed string or activates dispatch.
    """
    parsed = parse_protected_write_proposal(proposal, limits=policy.limits)
    origin = json.loads(admission.request_bytes)
    admitted = json.loads(admission.result_bytes)["body"]
    if (
        parsed.canonical_bytes != admission.proposal_bytes
        or parsed.proposal_sha256 != origin["proposal_sha256"]
        or policy.enrollment_revision != origin["enrollment_revision"]
        or authenticated_writer != (admitted["writer_principal"], admitted["writer_incarnation"])
    ):
        raise ValueError("writer preparation admission/enrollment/identity mismatch")
    verify_protected_effects(
        proposal,
        limits=policy.limits,
        primary_claims=policy.primary_claims,
        auxiliary_opcodes=policy.auxiliary_opcodes,
        enrolled_parents=policy.enrolled_parents,
    )
    paths = verify_protected_auxiliary_before_states(
        proposal,
        limits=policy.limits,
        inspections=inspections,
        enrolled_roots=policy.enrolled_roots,
    )
    observations = tuple((key, inspections.get(key)) for key in paths)
    deferred_parents = verify_protected_parent_bindings(
        proposal,
        limits=policy.limits,
        inspections=inspections,
        enrolled_roots=policy.enrolled_roots,
        enrolled_parents=policy.enrolled_parents,
    )
    content = bind_protected_write_content(
        proposal,
        limits=policy.limits,
        artifacts=artifacts,
        domain_verifiers=policy.domain_verifiers,
        max_artifact_bytes=policy.max_artifact_bytes,
    )
    return PreparedProtectedWrite(admission, policy, content, observations, deferred_parents)


def verify_prepared_begin(
    prepared: PreparedProtectedWrite,
    *,
    state: SynapseState,
    store: EventStore,
    authenticated_writer: tuple[str, str],
    authorize_current: Callable[[PreparedProtectedWrite, ProtectedWriteReservation], bool],
) -> ProtectedWriteReservation:
    """Verify current durable begin before the isolated executor accepts work.

    Parameters
    ----------
    prepared:
        Complete immutable inputs from the mandatory preparation entry point.
    state:
        Current authoritative state read while holding the existing actor lock.
    store:
        Same authoritative operation journal.
    authenticated_writer:
        Server-authenticated current principal/incarnation, never a client string.
    authorize_current:
        Trusted local check of current session/trust/ACL/enrollment and execution
        policy, returning exactly True. No external I/O or waiting under the lock.

    Returns
    -------
    ProtectedWriteReservation
        Exact current begun reservation. This is not a reusable execution token.

    Raises
    ------
    ValueError
        On stale custody, lineage, phase, writer, journal evidence or current policy.

    Notes
    -----
    The executor must still claim execution once in its protected service journal
    and retain/revalidate descriptors. This check performs no syscall, no begin
    mutation, no authentication itself and no renewal of historical authority.
    """
    reservation_id = prepared.admission.reservation_id
    current = state.protected_write_reservations.get(reservation_id)
    if (
        current is None
        or current.admission != prepared.admission
        or state.protected_write_admissions.get(reservation_id) != prepared.admission
        or reservation_id in state.protected_write_recoveries
        or state.protected_claim_custody.get(reservation_id) != current.custody
    ):
        raise ValueError("prepared writer has stale reservation custody")
    source = json.loads(current.request_bytes)
    body = json.loads(current.result_bytes)["body"]
    if (
        source["type"] != "protected_write_begin"
        or body["operation_phase"] != "executing"
        or body["revocation_phase"] != "open"
        or body["begin_sequence"] != current.transition_sequence
        or authenticated_writer != (body["writer_principal"], body["writer_incarnation"])
        or prepared.policy.enrollment_revision != source["enrollment_revision"]
        or prepared.content.proposal_sha256 != source["proposal_sha256"]
    ):
        raise ValueError("prepared writer has no matching current begin")
    parsed = parse_protected_write_request(current.request_bytes, limits=prepared.policy.limits)
    key = protected_write_operation_key(
        current.request_bytes,
        limits=prepared.policy.limits,
        authenticated_principal=authenticated_writer[0],
        authority_id=source["authority_id"],
        authority_continuity=source["authority_continuity"],
    )
    response = protected_operation_response(
        store,
        operation_key=key,
        request_digest=parsed.request_digest,
        mutation_sequence=current.transition_sequence,
    )
    if response.encode("ascii") != current.result_bytes:
        raise ValueError("current begin response differs from durable operation")
    if authorize_current(prepared, current) is not True:
        raise ValueError("current writer execution policy refused")
    return current
