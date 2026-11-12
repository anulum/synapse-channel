# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the closed, ordered answer to a request to open an attempt
"""The decision is a pure function of the request and the facts the hub read."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.operation_attempts import (
    ANSWERS,
    APPROVAL_EXPIRED,
    APPROVAL_STALE,
    APPROVED_FRESH,
    APPROVER_UNAUTHORISED,
    ATTEMPT_EXISTS,
    EARLIER_ATTEMPT_UNRECONCILED,
    NOT_APPROVED,
    NOT_CLAIM_HOLDER,
    OPERATION_CLOSED,
    STALE_EPOCH,
    UNKNOWN,
    ApprovalFact,
    AttemptFacts,
    AttemptRequest,
    OperationAttemptError,
    OperationFact,
    attempt_id,
    decide,
    decode_attempt_request,
    project_of_identity,
)

WORKER = "P/worker"
APPROVER = "P/approver"
DIGEST = "d" * 64
NOW = 1_800_000_000.0

REQUEST = AttemptRequest("T1", "op-1", 3, DIGEST, "key-1")
OPERATION = OperationFact("T1", WORKER, DIGEST, int(NOW) + 600, NOW - 60)
APPROVAL = ApprovalFact(41, APPROVER, False, ())
FRESH = AttemptFacts(
    claim_refusal=None,
    operation=OPERATION,
    gate_enabled=True,
    approval=APPROVAL,
    closed=False,
    attempt_open=False,
    approval_consumed=False,
    unreconciled=False,
    now=NOW,
)


def _frame(**changes: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "task_id": "T1",
        "operation_id": "op-1",
        "epoch": 3,
        "intent_sha256": DIGEST,
        "idem_key": "key-1",
    }
    frame.update(changes)
    return frame


def test_a_well_formed_request_decodes_whole() -> None:
    assert decode_attempt_request(_frame()) == REQUEST


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"epoch": True}, "epoch must be an integer from 1 to 9007199254740992"),
        ({"epoch": 0}, "epoch must be an integer from 1 to 9007199254740992"),
        ({"epoch": "3"}, "epoch must be an integer from 1 to 9007199254740992"),
        ({"task_id": ""}, "task_id must be text of 1 to 256 characters"),
        ({"task_id": "T\n1"}, "task_id cannot contain control characters"),
        ({"operation_id": "op 1"}, "operation_id has the wrong form"),
        ({"intent_sha256": "D" * 64}, "intent_sha256 has the wrong form"),
        ({"idem_key": None}, "idem_key must be text of 1 to 256 characters"),
        ({"idem_key": "k" * 257}, "idem_key must be text of 1 to 256 characters"),
    ],
)
def test_a_malformed_request_is_refused_without_echoing_it(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(OperationAttemptError) as refused:
        decode_attempt_request(_frame(**changes))
    assert str(refused.value) == message
    assert refused.value.code == "operation_attempt"


def test_the_answers_are_eleven_in_their_fixed_order() -> None:
    assert ANSWERS == (
        "not_claim_holder",
        "stale_epoch",
        "unknown",
        "not_approved",
        "approval_stale",
        "approval_expired",
        "approver_unauthorised",
        "operation_closed",
        "attempt_exists",
        "earlier_attempt_unreconciled",
        "approved_fresh",
    )


def _answer(facts: AttemptFacts, request: AttemptRequest = REQUEST) -> tuple[str, str]:
    decision = decide(request, facts, requester=WORKER)
    return decision.answer, decision.detail


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            replace(FRESH, claim_refusal=NOT_CLAIM_HOLDER),
            (
                NOT_CLAIM_HOLDER,
                "the requester does not hold the task's live claim as the operation's declarer",
            ),
        ),
        (
            replace(FRESH, operation=replace(OPERATION, declarer="P/other")),
            (
                NOT_CLAIM_HOLDER,
                "the requester does not hold the task's live claim as the operation's declarer",
            ),
        ),
        (
            replace(FRESH, claim_refusal=STALE_EPOCH),
            (STALE_EPOCH, "the stated epoch is neither the claim's epoch nor its ownership epoch"),
        ),
        (replace(FRESH, gate_enabled=False), (UNKNOWN, "the hub's verdict gate is off")),
        (
            replace(FRESH, now=OPERATION.declared_at - 1),
            (UNKNOWN, "the hub's clock reads earlier than the declaration"),
        ),
        (
            replace(FRESH, approval=replace(APPROVAL, bypassed=True)),
            (UNKNOWN, "the approval came through a bypassed verdict gate"),
        ),
        (
            replace(FRESH, operation=None),
            (NOT_APPROVED, "the operation is not declared on this task"),
        ),
        (
            replace(FRESH, operation=replace(OPERATION, task_id="T2")),
            (NOT_APPROVED, "the operation is not declared on this task"),
        ),
        (
            replace(FRESH, operation=replace(OPERATION, intent_sha256=None, deadline=None)),
            (NOT_APPROVED, "the operation was declared without an authorisation intent"),
        ),
        (
            replace(FRESH, operation=replace(OPERATION, intent_sha256="e" * 64)),
            (NOT_APPROVED, "the named digest is not the operation's intent"),
        ),
        (replace(FRESH, approval=None), (NOT_APPROVED, "the intent has no current approval")),
        (
            replace(FRESH, approval=replace(APPROVAL, gate_reasons=("P/rev: rejected", "P/x: y"))),
            (APPROVAL_STALE, "approval gate: P/rev: rejected; P/x: y"),
        ),
        (
            replace(FRESH, now=float(OPERATION.deadline or 0)),
            (APPROVAL_EXPIRED, "the hub's clock is at or past the intent's deadline"),
        ),
        (
            replace(FRESH, approval=replace(APPROVAL, approver=WORKER)),
            (APPROVER_UNAUTHORISED, "the approver is the intent's actor"),
        ),
        (
            replace(FRESH, approval=replace(APPROVAL, approver="Q/approver")),
            (APPROVER_UNAUTHORISED, "the approver is not of the actor's project"),
        ),
        (
            replace(FRESH, closed=True),
            (OPERATION_CLOSED, "an independent verification has verified the operation"),
        ),
        (
            replace(FRESH, attempt_open=True),
            (ATTEMPT_EXISTS, "an attempt of the operation is open"),
        ),
        (
            replace(FRESH, approval_consumed=True),
            (ATTEMPT_EXISTS, "this approval has already admitted an attempt"),
        ),
        (
            replace(FRESH, unreconciled=True),
            (
                EARLIER_ATTEMPT_UNRECONCILED,
                "an ended attempt has no independent observation since its end",
            ),
        ),
        (FRESH, (APPROVED_FRESH, "attempt opened")),
    ],
)
def test_each_answer_has_its_cause_and_its_whole_sentence(
    facts: AttemptFacts, expected: tuple[str, str]
) -> None:
    assert _answer(facts) == expected


def test_an_intent_without_a_deadline_never_expires() -> None:
    later = replace(FRESH, operation=replace(OPERATION, deadline=None), now=NOW + 10**9)
    assert _answer(later)[0] == APPROVED_FRESH


def test_the_first_answer_that_applies_wins() -> None:
    """Every cause at once gives the first answer; removing it gives the next."""
    everything = replace(
        FRESH,
        claim_refusal=NOT_CLAIM_HOLDER,
        gate_enabled=False,
        approval=replace(APPROVAL, gate_reasons=("stale",), approver=WORKER),
        closed=True,
        attempt_open=True,
        approval_consumed=True,
        unreconciled=True,
        now=float(OPERATION.deadline or 0),
    )
    seen = [_answer(everything)[0]]
    for change in (
        {"claim_refusal": None},
        {"gate_enabled": True},
        {"approval": replace(APPROVAL, approver=WORKER)},
        {"now": NOW},
        {"approval": APPROVAL},
        {"closed": False},
        {"attempt_open": False},
        {"approval_consumed": False},
        {"unreconciled": False},
    ):
        everything = replace(everything, **change)
        seen.append(_answer(everything)[0])
    assert seen == [
        NOT_CLAIM_HOLDER,
        UNKNOWN,
        APPROVAL_STALE,
        APPROVAL_EXPIRED,
        APPROVER_UNAUTHORISED,
        OPERATION_CLOSED,
        ATTEMPT_EXISTS,
        ATTEMPT_EXISTS,
        EARLIER_ATTEMPT_UNRECONCILED,
        APPROVED_FRESH,
    ]


def test_the_attempt_id_is_the_digest_of_the_operation_and_its_approval() -> None:
    document = {"approval_seq": 41, "operation_id": "op-1", "schema": "synapse.attempt-id.v0"}
    expected = hashlib.sha256(canonical_json(document)).hexdigest()[:32]
    assert attempt_id("op-1", 41) == expected == "1c939a816506396f7aa0a95d926299d9"
    assert attempt_id("op-1", 42) != expected
    assert attempt_id("op-2", 41) != expected


def test_the_project_of_an_identity_is_its_first_segment() -> None:
    assert project_of_identity("P/worker") == "P"
    assert project_of_identity("solo") == "solo"
