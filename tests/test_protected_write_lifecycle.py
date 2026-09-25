# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reservation lifecycle value regressions
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.protected_write_admission import bind_protected_write_admission
from synapse_channel.core.protected_write_lifecycle import (
    ProtectedTransitionContext,
    ProtectedWriteReservation,
    advance_protected_write_reservation,
)
from synapse_channel.core.protected_write_proposal import parse_protected_write_proposal
from synapse_channel.core.state import SynapseState
from test_protected_write_admission import CONTEXT, _admission, _claim
from test_protected_write_proposal import LIMITS
from test_protected_write_result import _result


def _initial(two_ops: bool = False) -> ProtectedWriteReservation:
    if not two_ops:
        return ProtectedWriteReservation.admitted(_admission())
    request, reply = _result("admit")
    proposal = cast(dict[str, Any], request["body"])["proposal"]
    path = proposal["auxiliary_operations"][0]["paths"][0]
    proposal["auxiliary_operations"].append(
        {
            "operation_id": "sync",
            "opcode": "fsync",
            "content_reference": None,
            "paths": [{**path, "before": path["after"]}],
        }
    )
    request["proposal_sha256"] = parse_protected_write_proposal(
        json.dumps(proposal), limits=LIMITS
    ).proposal_sha256
    reply["proposal_sha256"] = request["proposal_sha256"]
    cast(dict[str, Any], reply["body"])["request_digest"] = canonical_request_digest(request)
    admission = bind_protected_write_admission(
        json.dumps(request),
        json.dumps(reply),
        context=CONTEXT,
        claims={"task-1": _claim()},
        limits=LIMITS,
        reason_codes=frozenset(),
    )
    return ProtectedWriteReservation.admitted(admission)


def _frames(
    previous: ProtectedWriteReservation, verb: str, seq: int, **changes: object
) -> tuple[dict[str, Any], dict[str, Any], ProtectedTransitionContext]:
    request, _ = _result(verb)
    origin = json.loads(previous.admission.request_bytes)
    request["proposal_sha256"] = origin["proposal_sha256"]
    request["request_id"] = f"{verb}-{seq}"
    body = json.loads(previous.result_bytes)["body"]
    body.update(changes)
    body["request_type"] = request["type"]
    body["request_digest"] = canonical_request_digest(request)
    reply = {
        **request,
        "type": "protected_write_result",
        "sender": request["target"],
        "target": request["sender"],
        "body": body,
        "auth": json.loads(previous.result_bytes)["auth"],
    }
    context = ProtectedTransitionContext(
        str(request["sender"]),
        str(request["session_id"]),
        "writer" if verb in ("begin", "settle") else "author",
        seq,
    )
    return request, reply, context


def _go(
    previous: ProtectedWriteReservation,
    frames: tuple[dict[str, Any], dict[str, Any], ProtectedTransitionContext],
    *,
    quiescence: bool | None = None,
) -> ProtectedWriteReservation:
    request, reply, context = frames
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
        reply[field] = request[field]
    reply["sender"], reply["target"] = request["target"], request["sender"]
    reply["body"]["request_digest"] = canonical_request_digest(request)
    # Unit-only verifier result: no physical evidence/OS isolation is asserted.
    verifier = None if quiescence is None else lambda _admission, _raw, _result: quiescence
    return advance_protected_write_reservation(
        previous,
        json.dumps(request),
        json.dumps(reply),
        context=context,
        limits=LIMITS,
        reason_codes=frozenset(),
        verify_quiescence=verifier,
    )


def _begin(previous: ProtectedWriteReservation | None = None) -> ProtectedWriteReservation:
    previous = previous or _initial()
    return _go(
        previous, _frames(previous, "begin", 2, operation_phase="executing", begin_sequence=2)
    )


def _settle_frames(
    previous: ProtectedWriteReservation, outcome: str = "committed", seq: int = 3
) -> Any:
    request, reply, context = _frames(
        previous,
        "settle",
        seq,
        operation_phase="settled" if outcome in ("committed", "no_write") else "recovery_required",
        outcome=outcome,
        settlement_sequence=seq,
        evidence_reference=json.loads(previous.admission.proposal_bytes)["content_reference"],
    )
    request["body"]["outcome"] = outcome
    if outcome == "no_write":
        request["body"]["operation_results"][0]["status"] = "not_started"
    return request, reply, context


def test_state_transition_publishes_custody_closure_with_metadata_retained() -> None:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    initial = _initial()
    state.install_protected_write_admission(initial.admission, max_reservations=2)
    executing = _begin(initial)
    candidate = deepcopy(state)
    candidate.apply_protected_write_transition(initial, executing)
    assert state.protected_write_reservations["reservation"] == initial
    state.publish_from(candidate)
    assert state.protected_write_reservations["reservation"] == executing
    settled = _go(executing, _settle_frames(executing), quiescence=True)
    state.apply_protected_write_transition(executing, settled)
    assert state.protected_claim_custody == {}
    assert state.protected_write_admissions["reservation"] == initial.admission
    assert state.protected_write_reservations["reservation"] == settled
    effective = _go(
        settled,
        _frames(
            settled,
            "revoke",
            4,
            disposition="effective",
            revocation_phase="effective",
            revocation_sequence=4,
        ),
    )
    state.apply_protected_write_transition(settled, effective)
    assert state.protected_claim_custody == {}


@pytest.mark.parametrize(
    "case", ["stale", "changed", "same-sequence", "custody-missing", "resurrect"]
)
def test_state_transition_refuses_inconsistent_predecessors(case: str) -> None:
    state = SynapseState()
    state.claims["task-1"] = _claim()
    initial = _initial()
    state.install_protected_write_admission(initial.admission, max_reservations=2)
    previous, updated = initial, _begin(initial)
    if case == "stale":
        previous = updated
    elif case == "changed":
        updated = replace(updated, admission=replace(initial.admission, reservation_id="other"))
    elif case == "same-sequence":
        updated = replace(updated, transition_sequence=1)
    elif case == "custody-missing":
        state.protected_claim_custody.clear()
    else:
        state.apply_protected_write_transition(initial, updated)
        previous = _go(updated, _settle_frames(updated), quiescence=True)
        state.apply_protected_write_transition(updated, previous)
        updated = replace(updated, transition_sequence=4)
    before = deepcopy(state)
    with pytest.raises(ValueError):
        state.apply_protected_write_transition(previous, updated)
    assert state.protected_write_reservations == before.protected_write_reservations
    assert state.protected_claim_custody == before.protected_claim_custody


def test_known_settlement_and_revocation_are_distinct_transitions() -> None:
    initial = _initial()
    executing = _begin(initial)
    settled = _go(executing, _settle_frames(executing), quiescence=True)
    assert initial.holds_custody and executing.holds_custody
    assert not settled.holds_custody
    effective = _go(
        settled,
        _frames(
            settled,
            "revoke",
            4,
            disposition="effective",
            revocation_phase="effective",
            revocation_sequence=4,
        ),
    )
    assert not effective.holds_custody
    with pytest.raises(ValueError, match="terminal"):
        _go(effective, _frames(effective, "revoke", 5))


def test_prebegin_revocation_prevents_later_begin() -> None:
    initial = _initial()
    cancelled = _go(
        initial,
        _frames(
            initial,
            "revoke",
            2,
            disposition="effective",
            operation_phase="settled",
            revocation_phase="effective",
            revocation_sequence=2,
            outcome="no_write",
            settlement_sequence=2,
            evidence_reference=json.loads(initial.admission.proposal_bytes)["content_reference"],
        ),
    )
    assert not cancelled.holds_custody
    with pytest.raises(ValueError):
        _go(
            cancelled,
            _frames(
                cancelled,
                "begin",
                3,
                disposition="accepted",
                operation_phase="executing",
                begin_sequence=3,
                outcome=None,
                settlement_sequence=None,
                revocation_phase="open",
                revocation_sequence=None,
            ),
        )


def test_pending_revocation_waits_for_verified_settlement() -> None:
    executing = _begin()
    pending = _go(
        executing,
        _frames(
            executing,
            "revoke",
            3,
            disposition="pending",
            revocation_phase="requested",
            revocation_sequence=3,
        ),
    )
    repeated = _go(pending, _frames(pending, "revoke", 4))
    assert repeated.holds_custody
    frames = _settle_frames(repeated, seq=5)
    frames[1]["body"].update(
        revocation_phase="effective", revocation_sequence=5, disposition="accepted"
    )
    assert not _go(repeated, frames, quiescence=True).holds_custody


@pytest.mark.parametrize("outcome", ["partial", "unknown"])
def test_uncertain_settlement_retains_custody_and_cannot_restart_writer(outcome: str) -> None:
    executing = _begin()
    uncertain = _go(executing, _settle_frames(executing, outcome))
    assert uncertain.holds_custody
    with pytest.raises(ValueError, match="executing writer"):
        _go(uncertain, _settle_frames(uncertain, seq=4), quiescence=True)
    pending = _go(
        uncertain,
        _frames(
            uncertain,
            "revoke",
            4,
            disposition="pending",
            revocation_phase="requested",
            revocation_sequence=4,
        ),
    )
    assert pending.holds_custody


@pytest.mark.parametrize("evidence", [None, False])
def test_known_settlement_fails_without_verified_quiescence(evidence: bool | None) -> None:
    executing = _begin()
    with pytest.raises(ValueError, match="quiescence"):
        _go(executing, _settle_frames(executing), quiescence=evidence)


def test_verified_no_write_may_settle_after_begin() -> None:
    executing = _begin()
    assert not _go(executing, _settle_frames(executing, "no_write"), quiescence=True).holds_custody


@pytest.mark.parametrize("sequence", [True, 0, 1, 1.0, 1001])
def test_transition_rejects_invalid_or_reused_sequence(sequence: object) -> None:
    initial = _initial()
    request, reply, context = _frames(
        initial, "begin", 2, operation_phase="executing", begin_sequence=2
    )
    with pytest.raises(ValueError, match="journal sequence"):
        _go(initial, (request, reply, replace(context, event_sequence=sequence)))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["principal", "session_id"])
def test_transition_rejects_unbound_caller(field: str) -> None:
    initial = _initial()
    request, reply, context = _frames(
        initial, "begin", 2, operation_phase="executing", begin_sequence=2
    )
    with pytest.raises(ValueError, match="caller/session"):
        changed = (
            replace(context, principal="other")
            if field == "principal"
            else replace(context, session_id="other")
        )
        _go(initial, (request, reply, changed))


@pytest.mark.parametrize(
    "field",
    ["authority_id", "authority_continuity", "transaction_id", "enrollment_revision", "target"],
)
def test_transition_cannot_change_reservation_domain(field: str) -> None:
    initial = _initial()
    frames = _frames(initial, "begin", 2, operation_phase="executing", begin_sequence=2)
    frames[0][field] = "other"
    with pytest.raises(ValueError, match="immutable reservation"):
        _go(initial, frames)


def test_transition_refuses_changed_admission_sequence_wrong_role_and_unsupported_verb() -> None:
    initial = _initial()
    frames = _frames(
        initial, "begin", 2, operation_phase="executing", begin_sequence=2, admission_sequence=2
    )
    with pytest.raises(ValueError, match="admitted reservation identity"):
        _go(initial, frames)
    frames = _frames(initial, "begin", 2, operation_phase="executing", begin_sequence=2)
    with pytest.raises(ValueError, match="pinned writer"):
        _go(initial, (frames[0], frames[1], replace(frames[2], role="author")))
    with pytest.raises(ValueError, match="unsupported"):
        _go(initial, _frames(initial, "status", 2, disposition="known"))


def test_revocation_requires_author_session_or_explicit_operator_role() -> None:
    executing = _begin()
    frames = _frames(
        executing,
        "revoke",
        3,
        disposition="pending",
        revocation_phase="requested",
        revocation_sequence=3,
    )
    frames[0]["sender"] = "EXAMPLE/operator"
    context = replace(frames[2], principal="EXAMPLE/operator")
    with pytest.raises(ValueError, match="authorized operator"):
        _go(executing, (frames[0], frames[1], context))
    assert _go(executing, (frames[0], frames[1], replace(context, role="operator"))).holds_custody


def test_settlement_requires_exact_operations_and_completion() -> None:
    executing = _begin()
    frames = _settle_frames(executing)
    frames[0]["body"]["operation_results"][0]["operation_id"] = "other"
    with pytest.raises(ValueError, match="exact ordered"):
        _go(executing, frames, quiescence=True)
    frames = _settle_frames(executing)
    frames[0]["body"]["operation_results"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="incomplete operations"):
        _go(executing, frames, quiescence=True)


def test_settlement_cannot_report_work_after_failure() -> None:
    executing = _begin(_initial(two_ops=True))
    frames = _settle_frames(executing, "partial")
    first = frames[0]["body"]["operation_results"][0]
    frames[0]["body"]["operation_results"] = [
        {**first, "status": "failed"},
        {**first, "operation_id": "sync", "status": "completed"},
    ]
    with pytest.raises(ValueError, match="success-only"):
        _go(executing, frames)


def test_result_cannot_claim_a_different_transition() -> None:
    initial = _initial()
    with pytest.raises(ValueError, match="permitted reservation transition"):
        _go(initial, _frames(initial, "begin", 2, operation_phase="executing", begin_sequence=3))
