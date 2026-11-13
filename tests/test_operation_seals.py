# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the seal record: decoding, the hub's recomputation, the ledger's sealed state
"""A seal record names exactly one open attempt's seal; a kept seal ends it without reconciling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.attempt_markers import APPLIED, LANDED, OTHER_PARTY_FIRST, AttemptMarker
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    STATE_CLOSED,
    STATE_ENDED,
    STATE_OPEN,
    STATE_RECONCILED,
    STATE_SEALED,
    AttemptLedger,
    AttemptSummary,
)
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_records import (
    MAX_RECORDS_PER_OPERATION,
    REFUSED_RECORD_LIMIT,
    REFUSED_TASK_MISMATCH,
    REFUSED_UNKNOWN,
    OperationLedger,
    OperationRecordError,
    decode_operation_record,
    record_operation,
    seal_refusal,
)
from synapse_channel.core.operation_seals import (
    REFUSED_ATTEMPT_ENDED,
    REFUSED_ATTEMPT_UNKNOWN,
    REFUSED_SEAL_NOT_OBSERVED,
    SealRecord,
    decode_seal,
    seal_mismatch,
)
from synapse_channel.core.persistence import EventStore

OPERATION, TASK, ATTEMPT, DIGEST = "op:1", "T1", "a" * 32, "d" * 64
MARKER = AttemptMarker(OPERATION, ATTEMPT, DIGEST)


def _body(object_format: str = "sha1", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "attempt_id": ATTEMPT,
        "marker_ref": MARKER.ref,
        "object_format": object_format,
        "marker_object": MARKER.objects(object_format).sealed,
        "observation": LANDED,
    }
    body.update(changes)
    return body


def test_a_seal_names_exactly_this_attempts_sealed_blob_in_either_format() -> None:
    for object_format in ("sha1", "sha256"):
        seal = decode_seal(_body(object_format))
        assert seal.to_payload() == _body(object_format)
        assert seal_mismatch(seal, operation_id=OPERATION, intent_sha256=DIGEST) is None


FIELDS = "['attempt_id', 'marker_object', 'marker_ref', 'object_format', 'observation']"
ANSWERS = "['base_gone', 'landed', 'landed_earlier', 'not_reached', 'other_party_first', 'unknown']"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("x", "seal must be an object"),
        ({"attempt_id": ATTEMPT}, f"seal names exactly {FIELDS}"),
        ({**_body(), "extra": 1}, f"seal names exactly {FIELDS}"),
        (
            {**_body(), "object_format": "md5"},
            "seal object_format must be one of ['sha1', 'sha256']",
        ),
        (_body(observation="maybe"), f"seal observation must be one of {ANSWERS}"),
        (_body(attempt_id="A" * 32), "seal attempt_id has the wrong form"),
        (_body(marker_ref="refs/heads/main"), "seal marker_ref has the wrong form"),
        (_body(marker_object="0" * 64), "seal marker_object has the wrong form"),
    ],
)
def test_a_malformed_seal_is_refused_with_its_reason(body: object, message: str) -> None:
    with pytest.raises(ValueError) as refused:
        decode_seal(body)
    assert str(refused.value) == message


@pytest.mark.parametrize(
    ("changes", "digest"),
    [
        ({"observation": OTHER_PARTY_FIRST}, DIGEST),
        ({}, None),
        ({"marker_object": MARKER.objects("sha1").applied}, DIGEST),
        ({"marker_ref": "refs/synapse/attempts/" + "b" * 32}, DIGEST),
        ({}, "e" * 64),
    ],
)
def test_anything_but_this_attempts_sealed_blob_read_back_is_not_observed(
    changes: dict[str, Any], digest: str | None
) -> None:
    seal = decode_seal(_body(**changes))
    assert seal_mismatch(seal, operation_id=OPERATION, intent_sha256=digest) == (
        REFUSED_SEAL_NOT_OBSERVED
    )


def test_a_seal_record_frame_decodes_and_a_bad_body_is_an_operation_record_error() -> None:
    frame = {"record": "seal", "task_id": TASK, "operation_id": OPERATION, "seal": _body()}
    request = decode_operation_record(frame)
    assert (request.record, request.seal) == ("seal", SealRecord(**_body()))
    with pytest.raises(OperationRecordError) as refused:
        decode_operation_record({**frame, "seal": _body(observation="maybe")})
    assert str(refused.value).startswith("seal observation must be one of")
    with pytest.raises(OperationRecordError) as kinds:
        decode_operation_record({**frame, "record": "sealed"})
    assert (
        str(kinds.value) == "record must be one of ['declare', 'outcome', 'seal', 'verification']"
    )


def _ledger(*, opened: bool = True) -> OperationLedger:
    ledger = OperationLedger(None)
    ledger.declare(OPERATION, TASK, "git_publication")
    ledger.attempts.note_declaration(OPERATION, OperationFact(TASK, "P/worker", DIGEST, None, 1.0))
    if opened:
        ledger.attempts.open_attempt(OPERATION, ATTEMPT, executor="P/worker", approval_seq=7)
    return ledger


def _request(**frame: Any) -> Any:
    base = {"record": "seal", "task_id": TASK, "operation_id": OPERATION, "seal": _body()}
    return decode_operation_record({**base, **frame})


def test_every_refusal_of_a_seal_record_against_the_ledger() -> None:
    assert seal_refusal(_ledger(), _request()) is None
    assert seal_refusal(OperationLedger(None), _request()) == REFUSED_UNKNOWN
    assert seal_refusal(_ledger(), _request(task_id="T2")) == REFUSED_TASK_MISMATCH
    assert seal_refusal(_ledger(opened=False), _request()) == REFUSED_ATTEMPT_UNKNOWN
    ended = _ledger()
    ended.attempts.note_outcome(OPERATION, "P/worker", "not_started")
    assert seal_refusal(ended, _request()) == REFUSED_ATTEMPT_ENDED
    wrong = _request(seal=_body(marker_object=MARKER.objects("sha1").applied))
    assert seal_refusal(_ledger(), wrong) == REFUSED_SEAL_NOT_OBSERVED
    full = _ledger()
    for _ in range(MAX_RECORDS_PER_OPERATION):
        full.add_record(OPERATION)
    assert seal_refusal(full, _request()) == REFUSED_RECORD_LIMIT


def test_a_seal_ends_the_open_attempt_and_only_another_identity_reconciles_it() -> None:
    attempts = _ledger().attempts
    assert attempts.attempt_state(OPERATION, ATTEMPT) == STATE_OPEN
    attempts.note_seal(OPERATION, ATTEMPT)
    view = attempts.view(OPERATION, 7)
    assert (view.attempt_open, view.unreconciled) == (False, True)
    assert attempts.attempt_state(OPERATION, ATTEMPT) == STATE_SEALED
    attempts.note_outcome(OPERATION, "P/worker", "confirmed_done")
    assert attempts.attempt_state(OPERATION, ATTEMPT) == STATE_SEALED
    attempts.note_verification(OPERATION, "Q/checker", effect="absent", result="not_verified")
    assert attempts.attempt_state(OPERATION, ATTEMPT) == STATE_RECONCILED
    attempts.note_verification(OPERATION, "Q/checker", effect="absent", result="verified")
    assert attempts.summary(OPERATION) == (
        True,
        (AttemptSummary(ATTEMPT, "P/worker", STATE_CLOSED),),
    )


def test_a_seal_of_an_ended_or_unknown_attempt_changes_nothing() -> None:
    attempts = _ledger().attempts
    attempts.note_outcome(OPERATION, "P/worker", "confirmed_failed")
    attempts.note_seal(OPERATION, ATTEMPT)
    assert attempts.attempt_state(OPERATION, ATTEMPT) == STATE_ENDED
    attempts.note_seal("op:unknown", ATTEMPT)
    assert attempts.summary("op:unknown") is None
    assert attempts.summary(OPERATION) == (
        False,
        (AttemptSummary(ATTEMPT, "P/worker", STATE_ENDED),),
    )


def test_the_journal_keeps_the_seal_and_both_ledgers_rebuild_it(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(
            EventKind.OPERATION_DECLARED,
            {
                "task_id": TASK,
                "operation_id": OPERATION,
                "sender": "P/worker",
                "recorded_at": 1.0,
                "effect": "git_publication",
                "intent_sha256": DIGEST,
                "intent": {"deadline": 99},
            },
        )
        store.append(
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {
                "task_id": TASK,
                "operation_id": OPERATION,
                "sender": "P/worker",
                "answer": "approved_fresh",
                "attempt_id": ATTEMPT,
                "approval_seq": 7,
            },
        )
        seq = record_operation(store, _request(), sender="R/recovery", now=2.0)
        row = store.read_since(seq - 1, kinds=(EventKind.OPERATION_SEALED,))[0]
        restored = OperationLedger(store)
        alone = AttemptLedger(store)
    finally:
        store.close()
    assert row.payload == {
        "task_id": TASK,
        "operation_id": OPERATION,
        "sender": "R/recovery",
        "recorded_at": 2.0,
        **_body(),
    }
    assert restored.records_of(OPERATION) == 1
    assert restored.attempts.attempt_state(OPERATION, ATTEMPT) == STATE_SEALED
    assert alone.attempt_state(OPERATION, ATTEMPT) == STATE_SEALED
    assert MARKER.blob(APPLIED) != MARKER.blob("sealed")


def test_a_hub_that_requires_acl_maps_each_record_to_its_permission() -> None:
    from synapse_channel.core.acl import CLAIM, OPERATION_EVIDENCE, OPERATION_SEAL, Target
    from synapse_channel.core.handlers.operation_records import operation_record_access

    claim = Target("claim", TASK)
    assert operation_record_access({"record": "seal", "task_id": TASK}) == [(OPERATION_SEAL, claim)]
    assert operation_record_access({"record": "declare", "task_id": TASK}) == [(CLAIM, claim)]
    assert operation_record_access({"record": "outcome", "task_id": TASK}) == [
        (OPERATION_EVIDENCE, claim)
    ]
