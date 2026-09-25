# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live-witness protected admission binding
"""Bind an admission record without authenticating or executing a write."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.state_models import TaskClaim

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState


@dataclass(frozen=True)
class ProtectedAdmissionContext:
    """Server-resolved context, not a constructible authentication capability."""

    author_principal: str
    authority_id: str
    authority_continuity: str
    writer_principal: str
    writer_incarnation: str
    admission_sequence: int
    now: float


@dataclass(frozen=True)
class ProtectedWriteAdmission:
    """Immutable admission evidence and claim custody, never a begin permit."""

    reservation_id: str
    operation_key: str
    request_bytes: bytes
    result_bytes: bytes
    proposal_bytes: bytes
    claim_custody: tuple[ProtectedClaimCustody, ...]


def finalize_protected_write_admission(
    response: dict[str, object],
    sequences: tuple[int, ...],
    *,
    candidate: SynapseState,
    request: str | bytes,
    context: ProtectedAdmissionContext,
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
    max_reservations: int,
    sign_response: Callable[[dict[str, object]], Mapping[str, object]],
) -> Mapping[str, object]:
    """Finalize an admission inside the existing journal response transaction.

    Parameters
    ----------
    response:
        Private response draft supplied by EventStore, never a live shared dict.
    sequences:
        Actual journal mutation sequences; ordinary admission requires one event.
    candidate:
        Private state copy owned by the mutation actor, not the live hub state.
    request:
        Original authenticated admit request.
    context:
        Server context; its sequence is replaced by the actual journal sequence.
    limits:
        Explicit enrolled representation limits.
    reason_codes:
        Explicit enrolled result reason codes.
    max_reservations:
        Explicit retained-reservation budget.
    sign_response:
        Trusted local signer; no external I/O or live-state mutation permitted.

    Returns
    -------
    Mapping[str, object]
        Signed response committed with the event and operation record.

    Raises
    ------
    ValueError
        If sequence, response, witness, overlap or budget binding fails.

    Notes
    -----
    The journal rolls back on failure. Only the existing actor may publish the
    candidate after successful commit. This helper never enables wire dispatch.
    """
    if len(sequences) != 1:
        raise ValueError("ordinary admission requires exactly one mutation event")
    body = response.get("body")
    if not isinstance(body, dict):
        raise ValueError("admission response body must be an object")
    body["admission_sequence"] = sequences[0]
    signed = dict(sign_response(response))
    admission = bind_protected_write_admission(
        request,
        json.dumps(signed, allow_nan=False),
        context=replace(context, admission_sequence=sequences[0]),
        claims=candidate.claims,
        limits=limits,
        reason_codes=reason_codes,
    )
    candidate.install_protected_write_admission(admission, max_reservations=max_reservations)
    return signed


def bind_protected_write_admission(
    request: str | bytes,
    result: str | bytes,
    *,
    context: ProtectedAdmissionContext,
    claims: Mapping[str, TaskClaim],
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
) -> ProtectedWriteAdmission:
    """Bind exact current claims and writer identity to a journal-sequenced admission.

    Parameters
    ----------
    request:
        Original admit request JSON, already cryptographically authenticated.
    result:
        Final response JSON, signed by the trusted authority.
    context:
        Server-resolved identities, actual journal sequence and current clock.
    claims:
        Authoritative candidate claims accessed within the mutation actor.
    limits:
        Explicit enrolled representation budgets.
    reason_codes:
        Explicit enrolled response reason vocabulary.

    Returns
    -------
    ProtectedWriteAdmission
        Immutable evidence retaining the complete request/result/proposal bytes.

    Raises
    ------
    ValueError
        On invalid wire data, stale/mismatched claims, writer or journal binding.

    Notes
    -----
    The caller must verify signatures, active session, freshness, enrollment,
    root/effect coverage and overlapping reservations. This function performs no
    I/O and mutates no state. Construction alone confers no execution authority.
    A recovery admission has different custody rules and is not accepted here.
    """
    return _bind_protected_admission(
        request,
        result,
        context=context,
        claims=claims,
        limits=limits,
        reason_codes=reason_codes,
        request_type="protected_write_admit",
    )


def _bind_protected_admission(
    request: str | bytes,
    result: str | bytes,
    *,
    context: ProtectedAdmissionContext,
    claims: Mapping[str, TaskClaim],
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
    request_type: str,
) -> ProtectedWriteAdmission:
    """Share exact fresh-claim binding, without bypassing verb-specific custody."""
    if (
        type(context.now) is not float
        or not math.isfinite(context.now)
        or context.now < 0
        or context.now >= 2**53
        or math.copysign(1.0, context.now) < 0
    ):
        raise ValueError("invalid admission clock")
    if type(context.admission_sequence) is not int or not (
        0 < context.admission_sequence <= limits.max_counter
    ):
        raise ValueError("invalid admission journal sequence")
    operation_key = protected_write_operation_key(
        request,
        limits=limits,
        authenticated_principal=context.author_principal,
        authority_id=context.authority_id,
        authority_continuity=context.authority_continuity,
    )
    parsed = parse_protected_write_request(request, limits=limits)
    source = json.loads(parsed.canonical_bytes)
    if source["type"] != request_type or parsed.proposal is None:
        raise ValueError("an ordinary admission requires an admit proposal")
    response = parse_protected_write_result(
        result, request=request, limits=limits, reason_codes=reason_codes
    )
    body = json.loads(response.canonical_bytes)["body"]
    if (
        body["disposition"] != "accepted"
        or body["operation_phase"] != "admitted"
        or body["revocation_phase"] != "open"
        or body["revocation_sequence"] is not None
        or body["admission_sequence"] != context.admission_sequence
        or body["writer_principal"] != context.writer_principal
        or body["writer_incarnation"] != context.writer_incarnation
        or context.writer_principal == context.author_principal
    ):
        raise ValueError("admission response does not match current writer or journal")
    proposal = json.loads(parsed.proposal.canonical_bytes)
    custody: list[ProtectedClaimCustody] = []
    for witness in proposal["claims"]:
        claim = claims.get(witness["task_id"])
        if (
            claim is None
            or claim.task_id != witness["task_id"]
            or claim.owner != context.author_principal
            or claim.owner != witness["owner"]
            or type(claim.epoch) is not int
            or claim.epoch != witness["epoch"]
            or type(claim.version) is not int
            or claim.version != witness["version"]
            or type(claim.lease_expires_at) is not float
            or claim.lease_expires_at != witness["lease_expires_at"]
            or claim.lease_expires_at <= context.now
        ):
            raise ValueError("admission claim witness is missing, stale or not author-owned")
        custody.append(ProtectedClaimCustody.capture(claim))
    return ProtectedWriteAdmission(
        cast(str, body["reservation_id"]),
        operation_key,
        parsed.canonical_bytes,
        response.canonical_bytes,
        parsed.proposal.canonical_bytes,
        tuple(custody),
    )
