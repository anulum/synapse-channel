# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.protected_write_admission import (
    ProtectedAdmissionContext,
    ProtectedWriteAdmission,
)
from synapse_channel.core.protected_write_custody import ProtectedClaimCustody
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.protected_write_recovery import (
    ProtectedRecoveryAdmission,
    bind_protected_recovery,
)
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import TaskClaim
from test_protected_write_admission import CONTEXT, _claim
from test_protected_write_lifecycle import _frames, _go, _initial
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result


def _blocked() -> ProtectedWriteReservation:
    parent = _initial()
    parent = _go(
        parent,
        _frames(
            parent,
            "begin",
            2,
            disposition="accepted",
            operation_phase="executing",
            begin_sequence=2,
        ),
    )
    frames = _frames(
        parent,
        "settle",
        3,
        disposition="accepted",
        operation_phase="recovery_required",
        outcome="unknown",
        settlement_sequence=3,
    )
    frames[0]["body"]["outcome"] = "unknown"
    return _go(parent, frames)


def _inputs() -> tuple[
    ProtectedWriteReservation, dict[str, Any], dict[str, Any], ProtectedAdmissionContext, TaskClaim
]:
    parent = _blocked()
    raw_request, raw_response = _result("recover")
    request = cast(dict[str, Any], raw_request)
    response = cast(dict[str, Any], raw_response)
    request.update(sender="EXAMPLE/recovery", transaction_id="recovery-transaction")
    request["body"]["parent_reservation_id"] = parent.admission.reservation_id
    proposal = request["body"]["proposal"]
    claim = replace(_claim(), task_id="recovery-claim", owner="EXAMPLE/recovery")
    proposal["claims"][0].update(task_id=claim.task_id, owner=claim.owner)
    for operation in proposal["operations"]:
        operation["claim_task_id"] = claim.task_id
    context = replace(
        CONTEXT,
        author_principal=claim.owner,
        admission_sequence=4,
        writer_incarnation="recovery-incarnation",
    )
    response["body"].update(
        reservation_id="recovery-reservation",
        admission_sequence=4,
        writer_incarnation=context.writer_incarnation,
    )
    return parent, request, response, context, claim


def _sync(request: dict[str, Any], response: dict[str, Any]) -> None:
    request["proposal_sha256"] = parse_protected_write_proposal(
        json.dumps(request["body"]["proposal"]), limits=LIMITS
    ).proposal_sha256
    for field in (
        "sender",
        "target",
        "session_id",
        "authority_id",
        "authority_continuity",
        "transaction_id",
        "proposal_sha256",
        "enrollment_revision",
        "request_id",
    ):
        response[field] = request[field]
    response["sender"], response["target"] = request["target"], request["sender"]
    response["body"]["request_digest"] = canonical_request_digest(request)


def test_recovery_binds_parent_and_fresh_service_without_mutation() -> None:
    parent, request, response, context, claim = _inputs()
    _sync(request, response)
    seen: list[tuple[ProtectedWriteReservation, ProtectedWriteAdmission]] = []

    def verify(old: ProtectedWriteReservation, child: ProtectedWriteAdmission) -> bool:
        seen.append((old, child))
        return True

    bound = bind_protected_recovery(
        parent,
        json.dumps(request),
        json.dumps(response),
        context=context,
        claims={claim.task_id: claim},
        limits=LIMITS,
        reason_codes=frozenset(),
        verify_recovery=verify,
    )
    assert seen == [(parent, bound.admission)]
    assert bound.parent is parent
    assert parent.holds_custody
    assert bound.admission.claim_custody[0].owner == "EXAMPLE/recovery"


def _transfer_inputs() -> tuple[SynapseState, ProtectedRecoveryAdmission]:
    parent, request, response, context, claim = _inputs()
    _sync(request, response)
    recovery = bind_protected_recovery(
        parent,
        json.dumps(request),
        json.dumps(response),
        context=context,
        claims={claim.task_id: claim},
        limits=LIMITS,
        reason_codes=frozenset(),
        # Unit-only positive recovery callback; activation needs operator-reviewed predecessor and
        # candidate files after interruption.
        verify_recovery=lambda _p, _a: True,
    )
    state = SynapseState()
    state.claims[claim.task_id] = claim
    state.protected_write_admissions[parent.admission.reservation_id] = parent.admission
    state.protected_write_reservations[parent.admission.reservation_id] = parent
    state.protected_claim_custody[parent.admission.reservation_id] = parent.custody
    return state, recovery


def test_transfer_preserves_ancestor_domain_and_refuses_late_parent() -> None:
    state, recovery = _transfer_inputs()
    candidate = deepcopy(state)
    candidate.transfer_protected_write_custody(recovery, max_reservations=2)
    child_id = recovery.admission.reservation_id
    parent_id = recovery.parent.admission.reservation_id
    assert child_id not in state.protected_write_reservations
    state.publish_from(candidate)
    child = state.protected_write_reservations[child_id]
    assert state.protected_write_recoveries == {parent_id: child_id}
    assert state.protected_claim_custody == {child_id: child.custody}
    assert child.inherited_custody == recovery.parent.custody
    assert state.protected_write_reservations[parent_id] == recovery.parent
    with pytest.raises(ValueError, match="predecessor"):
        state.apply_protected_write_transition(
            recovery.parent, replace(recovery.parent, transition_sequence=5)
        )
    with pytest.raises(ValueError, match="parent"):
        state.transfer_protected_write_custody(recovery, max_reservations=3)
    # Scope checks retain the expired original task without renewing its lease.
    assert "task-1" not in state.claims
    assert any(w.task_id == "task-1" for w in state.protected_claim_custody[child_id])
    for verb, sequence, changes in (
        (
            "begin",
            5,
            {"disposition": "accepted", "operation_phase": "executing", "begin_sequence": 5},
        ),
        (
            "settle",
            6,
            {
                "disposition": "accepted",
                "operation_phase": "settled",
                "outcome": "committed",
                "settlement_sequence": 6,
            },
        ),
    ):
        frames = _frames(child, verb, sequence, **changes)
        origin = json.loads(child.admission.request_bytes)
        frames[0]["transaction_id"] = origin["transaction_id"]
        frames[0]["body"].update(reservation_id=child_id, writer_incarnation="recovery-incarnation")
        if verb == "settle":
            frames[1]["body"]["evidence_reference"] = frames[0]["body"]["quiescence_reference"]
        updated = _go(child, frames, quiescence=True)
        assert updated.inherited_custody == child.inherited_custody
        with pytest.raises(ValueError, match="predecessor"):
            state.apply_protected_write_transition(child, replace(updated, inherited_custody=()))
        state.apply_protected_write_transition(child, updated)
        child = updated
    assert state.protected_claim_custody == {}
    assert state.protected_write_recoveries == {parent_id: child_id}


@pytest.mark.parametrize(
    "case",
    [
        "parent",
        "custody",
        "budget-bool",
        "budget-zero",
        "quota",
        "duplicate",
        "empty",
        "missing",
        "changed",
        "overlap",
        "other-domain",
    ],
)
def test_transfer_is_all_or_nothing(case: str) -> None:
    state, recovery = _transfer_inputs()
    budget = 4
    if case == "parent":
        state.protected_write_reservations.clear()
    elif case == "custody":
        state.protected_claim_custody.clear()
    elif case == "budget-bool":
        budget = True
    elif case == "budget-zero":
        budget = 0
    elif case == "quota":
        budget = 1
    elif case == "duplicate":
        state.protected_write_admissions[recovery.admission.reservation_id] = recovery.admission
    elif case == "empty":
        recovery = replace(recovery, admission=replace(recovery.admission, claim_custody=()))
    elif case == "missing":
        state.claims.clear()
    elif case == "changed":
        state.claims["recovery-claim"].version += 1
    elif case in ("overlap", "other-domain"):
        held = recovery.parent.custody[0]
        if case == "other-domain":
            held = replace(held, task_id="unrelated", worktree="/different", paths=("other",))
        state.protected_claim_custody["other"] = (held,)
    before = deepcopy(state)
    if case == "other-domain":
        state.transfer_protected_write_custody(recovery, max_reservations=budget)
        assert state.protected_claim_custody["other"] == before.protected_claim_custody["other"]
    else:
        with pytest.raises(ValueError):
            state.transfer_protected_write_custody(recovery, max_reservations=budget)
        assert state.protected_write_admissions == before.protected_write_admissions
        assert state.protected_write_reservations == before.protected_write_reservations
        assert state.protected_claim_custody == before.protected_claim_custody
        assert state.protected_write_recoveries == before.protected_write_recoveries


@pytest.mark.parametrize(
    "case",
    [
        "phase",
        "effective",
        "parent",
        "reservation",
        "transaction",
        "author",
        "incarnation",
        "sequence",
        "target",
        "enrollment_revision",
        "target_project",
        "recovery_policy_revision",
        "claim",
        "proof",
        "proof-integer",
        "ancestor-claim",
        "expired",
    ],
)
def test_recovery_refuses_unproven_or_reused_authority(case: str) -> None:
    parent, request, response, context, claim = _inputs()
    proof: object = True
    if case in ("phase", "effective"):
        old = json.loads(parent.result_bytes)
        old["body"]["operation_phase" if case == "phase" else "revocation_phase"] = (
            "settled" if case == "phase" else "effective"
        )
        parent = replace(parent, result_bytes=json.dumps(old).encode())
    elif case == "parent":
        request["body"]["parent_reservation_id"] = "foreign"
    elif case == "reservation":
        response["body"]["reservation_id"] = parent.admission.reservation_id
    elif case == "transaction":
        request["transaction_id"] = json.loads(parent.admission.request_bytes)["transaction_id"]
    elif case == "author":
        request["sender"] = claim.owner = "EXAMPLE/author"
        request["body"]["proposal"]["claims"][0]["owner"] = claim.owner
        context = replace(context, author_principal=claim.owner)
    elif case == "incarnation":
        response["body"]["writer_incarnation"] = "incarnation"
        context = replace(context, writer_incarnation="incarnation")
    elif case == "sequence":
        response["body"]["admission_sequence"] = 3
        context = replace(context, admission_sequence=3)
    elif case in ("target", "enrollment_revision"):
        request[case] = "changed"
    elif case in ("target_project", "recovery_policy_revision"):
        request["body"]["proposal"][case] = "changed"
    elif case == "claim":
        claim.task_id = "task-1"
        request["body"]["proposal"]["claims"][0]["task_id"] = claim.task_id
        request["body"]["proposal"]["operations"][0]["claim_task_id"] = claim.task_id
    elif case in ("proof", "proof-integer"):
        proof = False if case == "proof" else 1
    elif case == "ancestor-claim":
        parent = replace(parent, inherited_custody=(ProtectedClaimCustody.capture(claim),))
    elif case == "expired":
        context = replace(context, now=claim.lease_expires_at)
    _sync(request, response)
    with pytest.raises(ValueError):
        bind_protected_recovery(
            parent,
            json.dumps(request),
            json.dumps(response),
            context=context,
            claims={claim.task_id: claim},
            limits=LIMITS,
            reason_codes=frozenset(),
            verify_recovery=lambda _p, _a: cast(bool, proof),
        )
