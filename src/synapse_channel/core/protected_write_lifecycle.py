# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reservation transition binding
"""Validate reservation transitions without executing writes or releasing state."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from synapse_channel.core.protected_write_admission import ProtectedWriteAdmission
from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.protected_write_result import parse_protected_write_result


@dataclass(frozen=True)
class ProtectedWriteReservation:
    """Immutable admitted identity and latest validated state, not a write permit."""

    admission: ProtectedWriteAdmission
    request_bytes: bytes
    result_bytes: bytes
    transition_sequence: int
    inherited_custody: tuple[ProtectedClaimCustody, ...] = ()

    @property
    def custody(self) -> tuple[ProtectedClaimCustody, ...]:
        """Retain the whole ancestor domain as well as fresh service witnesses."""
        return self.inherited_custody + self.admission.claim_custody

    @classmethod
    def admitted(cls, admission: ProtectedWriteAdmission) -> ProtectedWriteReservation:
        """Initialize from an already validated, durably installed admission."""
        body = json.loads(admission.result_bytes)["body"]
        return cls(
            admission, admission.request_bytes, admission.result_bytes, body["admission_sequence"]
        )

    @property
    def holds_custody(self) -> bool:
        """Keep custody unless the validated phase is settled with known outcome."""
        body = json.loads(self.result_bytes)["body"]
        return body["operation_phase"] != "settled" or body["outcome"] not in (
            "committed",
            "no_write",
        )


@dataclass(frozen=True)
class ProtectedTransitionContext:
    """Server-resolved caller/session/role and actual transition event sequence."""

    principal: str
    session_id: str
    role: Literal["author", "writer", "operator"]
    event_sequence: int


def advance_protected_write_reservation(
    previous: ProtectedWriteReservation,
    request: str | bytes,
    result: str | bytes,
    *,
    context: ProtectedTransitionContext,
    limits: ProtectedWriteProposalLimits,
    reason_codes: frozenset[str],
    verify_quiescence: Callable[[ProtectedWriteAdmission, bytes, bytes], bool] | None = None,
) -> ProtectedWriteReservation:
    """Bind a begin, settle or revoke result to one existing reservation.

    Parameters
    ----------
    previous:
        Current immutable reservation from authoritative state.
    request:
        Original authenticated transition request.
    result:
        Authority result, with its actual journal sequence already bound.
    context:
        Server-resolved identity and role, never copied from client metadata.
    limits:
        Exact enrolled representation budgets.
    reason_codes:
        Enrolled result vocabulary.
    verify_quiescence:
        Trusted local verifier of immutable settlement evidence bound to the
        admission and canonical request/result. Must return exactly True for known
        settlement. No filesystem/network work or waiting under the actor lock.

    Returns
    -------
    ProtectedWriteReservation
        A new value; this function publishes no state or execution permission.

    Raises
    ------
    ValueError
        On identity, state, sequence, operation-result or quiescence mismatch.

    Notes
    -----
    Active trust/session/freshness and operator authorization precede entry.
    Recovery requires a separately admitted parent-bound transaction; this API
    cannot convert uncertain custody into a new writer's authority.

    Prebegin revocation is effective only within this authority protocol:
    begin requires admitted/open state, and transition publication compares the
    exact predecessor, so begin and revoke cannot both win that state. The
    writer driver must recheck the exact current begin before every effect.
    Once begin succeeds, revocation remains pending until a known settlement
    with verified quiescence. These state invariants do not prove an external
    writer cannot bypass the driver; activation requires a supervised writer
    and a real multi-process no-write proof.
    """
    seq = context.event_sequence
    if type(seq) is not int or not previous.transition_sequence < seq <= limits.max_counter:
        raise ValueError("transition requires a new bounded journal sequence")
    parsed = parse_protected_write_request(request, limits=limits)
    reply = parse_protected_write_result(
        result, request=request, limits=limits, reason_codes=reason_codes
    )
    source = json.loads(parsed.canonical_bytes)
    origin = json.loads(previous.admission.request_bytes)
    old = json.loads(previous.result_bytes)["body"]
    body = json.loads(reply.canonical_bytes)["body"]
    if source["sender"] != context.principal or source["session_id"] != context.session_id:
        raise ValueError("transition caller/session mismatch")
    for field in (
        "authority_id",
        "authority_continuity",
        "transaction_id",
        "proposal_sha256",
        "enrollment_revision",
        "target",
    ):
        if source[field] != origin[field]:
            raise ValueError("transition immutable reservation binding mismatch")
    for field in ("reservation_id", "writer_principal", "writer_incarnation", "admission_sequence"):
        if body[field] != old[field]:
            raise ValueError("transition changed admitted reservation identity")
    verb = source["type"].removeprefix("protected_write_")
    if verb not in ("begin", "settle", "revoke"):
        raise ValueError("unsupported reservation transition")
    expected = dict(old)
    if verb in ("begin", "settle"):
        if context.role != "writer" or context.principal != old["writer_principal"]:
            raise ValueError("only the pinned writer may begin or settle")
    elif context.role != "operator" and not (
        context.role == "author"
        and context.principal == origin["sender"]
        and context.session_id == origin["session_id"]
    ):
        raise ValueError("revocation requires the admitted author or an authorized operator")

    if verb == "begin":
        if old["operation_phase"] != "admitted" or old["revocation_phase"] != "open":
            raise ValueError("reservation cannot begin")
        expected.update(operation_phase="executing", begin_sequence=seq, disposition="accepted")
    elif verb == "settle":
        if old["operation_phase"] != "executing":
            raise ValueError("settlement requires the executing writer")
        outcome = source["body"]["outcome"]
        proposal = json.loads(previous.admission.proposal_bytes)
        results = source["body"]["operation_results"]
        if [item["operation_id"] for item in results] != [
            item["operation_id"] for item in proposal["auxiliary_operations"]
        ]:
            raise ValueError("settlement must cover exact ordered executable operations")
        stopped = False
        for item in results:
            if stopped and item["status"] != "not_started":
                raise ValueError("settlement violates success-only execution ordering")
            stopped = item["status"] != "completed"
        if outcome == "committed" and stopped:
            raise ValueError("committed settlement contains incomplete operations")
        known = outcome in ("committed", "no_write")
        if known and (
            verify_quiescence is None
            or verify_quiescence(previous.admission, parsed.canonical_bytes, reply.canonical_bytes)
            is not True
        ):
            raise ValueError("known settlement requires verified quiescence evidence")
        expected.update(
            operation_phase="settled" if known else "recovery_required",
            outcome=outcome,
            settlement_sequence=seq,
            disposition="accepted",
        )
        if known and old["revocation_phase"] == "requested":
            expected.update(revocation_phase="effective", revocation_sequence=seq)
    else:
        if old["revocation_phase"] == "effective":
            raise ValueError("effective revocation is already terminal")
        if old["operation_phase"] == "admitted":
            expected.update(
                operation_phase="settled",
                outcome="no_write",
                settlement_sequence=seq,
                revocation_phase="effective",
                revocation_sequence=seq,
                disposition="effective",
            )
        elif old["operation_phase"] == "settled":
            expected.update(
                revocation_phase="effective", revocation_sequence=seq, disposition="effective"
            )
        else:
            expected.update(
                revocation_phase="requested",
                disposition="pending",
                revocation_sequence=old["revocation_sequence"]
                if old["revocation_phase"] == "requested"
                else seq,
            )
    for field in (
        "operation_phase",
        "revocation_phase",
        "outcome",
        "begin_sequence",
        "settlement_sequence",
        "revocation_sequence",
        "disposition",
    ):
        if body[field] != expected[field]:
            raise ValueError("result does not describe the permitted reservation transition")
    return ProtectedWriteReservation(
        previous.admission,
        parsed.canonical_bytes,
        reply.canonical_bytes,
        seq,
        previous.inherited_custody,
    )
