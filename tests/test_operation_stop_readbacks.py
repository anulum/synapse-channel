# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what a stop read back, decoded, checked and kept by the ledger
"""A read-back is decoded strictly, checked against what the hub recomputes, and only a
``sealed`` or ``applied`` one closes an attempt for its stop."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.attempt_markers import AttemptMarker
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_records import (
    MAX_RECORDS_PER_OPERATION,
    OperationLedger,
    decode_operation_record,
    kept_stop_readback,
    stop_readback_refusal,
)
from synapse_channel.core.operation_stop_readbacks import (
    REFUSED_ATTEMPT_OPEN,
    REFUSED_READBACK_MISMATCH,
    StopReadback,
    decode_stop_readback,
    marker_reads_sealed,
    readback_mismatch,
    recompute_readback_result,
)
from synapse_channel.core.persistence import EventStore

OPERATION = "git-op"
ATTEMPT = "a" * 32
DIGEST = "d" * 64
MARKER = AttemptMarker(OPERATION, ATTEMPT, DIGEST)
OBJECTS = MARKER.objects("sha1")


def _body(result: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"stop_seq": 7, "attempt_id": ATTEMPT, "result": result}
    if result in ("sealed", "applied"):
        body.update(
            marker_ref=MARKER.ref,
            object_format="sha1",
            marker_object=OBJECTS.sealed if result == "sealed" else OBJECTS.applied,
        )
    body.update(changes)
    return body


def test_each_result_decodes_whole_and_its_payload_keeps_only_what_it_names() -> None:
    sealed = decode_stop_readback(_body("sealed"))
    assert sealed == StopReadback(7, ATTEMPT, "sealed", "", MARKER.ref, "sha1", OBJECTS.sealed)
    unread = decode_stop_readback(_body("unread", reason="not_reached"))
    assert unread.to_payload() == {
        "stop_seq": 7,
        "attempt_id": ATTEMPT,
        "result": "unread",
        "reason": "not_reached",
    }
    seen = decode_stop_readback(
        _body(
            "unread",
            reason="unknown",
            marker_ref=MARKER.ref,
            object_format="sha1",
            marker_object="f" * 40,
        )
    )
    assert seen.marker_object == "f" * 40
    assert decode_stop_readback(_body("unsealable")).to_payload()["result"] == "unsealable"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("x", "readback must be an object"),
        (_body("sealed", extra=1), "readback names only"),
        (_body("sealed", stop_seq=0), "readback stop_seq must be a journal sequence"),
        (_body("sealed", stop_seq=True), "readback stop_seq must be a journal sequence"),
        (_body("sealed", attempt_id="A" * 32), "readback attempt_id has the wrong form"),
        (_body("lost"), "readback result must be one of"),
        (_body("unread"), "an unread readback names its reason"),
        (_body("unread", reason="landed"), "an unread readback names its reason"),
        (_body("sealed", reason="unknown"), "only an unread readback names a reason"),
        (_body("unsealable", marker_ref=MARKER.ref), "an unsealable readback names no marker"),
        (
            {"stop_seq": 7, "attempt_id": ATTEMPT, "result": "sealed"},
            "readback names marker_ref, object_format and marker_object together",
        ),
        (_body("sealed", object_format="md5"), "readback object_format must be one of"),
        (_body("sealed", marker_ref="refs/heads/main"), "readback marker_ref has the wrong form"),
        (_body("sealed", marker_object="0" * 64), "readback marker_object has the wrong form"),
    ],
)
def test_a_malformed_readback_is_refused_with_its_reason(body: object, message: str) -> None:
    with pytest.raises(ValueError) as refused:
        decode_stop_readback(body)
    assert str(refused.value).startswith(message)


def _check(
    body: dict[str, Any],
    *,
    marked: bool = True,
    attempt_open: bool = False,
    digest: str | None = DIGEST,
) -> str | None:
    return readback_mismatch(
        decode_stop_readback(body),
        operation_id=OPERATION,
        intent_sha256=digest,
        marked=marked,
        attempt_open=attempt_open,
    )


def test_the_hub_recomputes_each_marker_and_decides_unsealable_from_the_declaration() -> None:
    assert _check(_body("sealed")) is None
    assert _check(_body("applied")) is None
    assert _check(_body("sealed", marker_object=OBJECTS.applied)) == REFUSED_READBACK_MISMATCH
    assert _check(_body("applied", marker_object=OBJECTS.sealed)) == REFUSED_READBACK_MISMATCH
    other = AttemptMarker(OPERATION, "b" * 32, DIGEST).ref
    assert _check(_body("sealed", marker_ref=other)) == REFUSED_READBACK_MISMATCH
    assert _check(_body("sealed"), digest=None) == REFUSED_READBACK_MISMATCH
    assert _check(_body("sealed"), attempt_open=True) == REFUSED_ATTEMPT_OPEN
    assert _check(_body("applied"), attempt_open=True) is None
    assert _check(_body("unsealable"), marked=False) is None
    assert _check(_body("unsealable")) == REFUSED_READBACK_MISMATCH
    assert _check(_body("unread", reason="base_gone")) is None
    assert _check(_body("unread", reason="base_gone"), marked=False) == REFUSED_READBACK_MISMATCH


def test_only_sealed_or_applied_read_backs_close_a_stop() -> None:
    ledger = AttemptLedger(None)
    ledger.note_stop("project:P", 7, epoch=1, listed=(("op-1", "a1"), ("op-2", "b1")))
    ledger.note_stop("project:Q", 9, epoch=1, listed=())
    assert ledger.stop_lists(7, "op-1", "a1") and not ledger.stop_lists(7, "op-1", "b1")
    assert not ledger.stop_lists(8, "op-1", "a1")
    assert ledger.stop_closed(9) and not ledger.stop_closed(8)
    assert not ledger.stop_closed(7)
    ledger.note_readback(7, "a1", "sealed")
    ledger.note_readback(7, "b1", "unread")
    ledger.note_readback(8, "a1", "sealed")
    assert (ledger.readback_of(7, "b1"), ledger.readback_of(8, "a1")) == ("unread", None)
    assert not ledger.stop_closed(7)
    ledger.note_readback(7, "b1", "applied")
    assert ledger.stop_closed(7)
    candidate = copy.deepcopy(ledger)
    candidate.note_readback(9, "x", "sealed")
    fresh = AttemptLedger(None)
    fresh.publish_from(candidate)
    assert fresh.stop_closed(7) and fresh.readback_of(9, "x") == "sealed"


def test_the_read_backs_are_restored_and_a_malformed_row_is_skipped(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        listed = [{"operation_id": "op-1", "attempt_id": "a1", "task_id": "T1", "executor": "P/w"}]
        stopped = store.append(
            EventKind.OPERATION_STOPPED, {"scope": "project:P", "epoch": 1, "listed": listed}
        )
        base = {"operation_id": "op-1", "sender": "R/r"}
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {**base, "stop_seq": stopped, "attempt_id": "a1", "result": "unread"},
        )
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {**base, "stop_seq": True, "attempt_id": "a1", "result": "sealed"},
        )
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {**base, "stop_seq": stopped, "attempt_id": "a1", "result": "applied"},
        )
        restored = AttemptLedger(store)
    finally:
        store.close()
    assert restored.stop_lists(stopped, "op-1", "a1")
    assert restored.readback_of(stopped, "a1") == "applied"
    assert restored.stop_closed(stopped)


def test_a_read_back_past_the_operation_s_record_limit_is_refused() -> None:
    ledger = OperationLedger(None)
    ledger.declare(OPERATION, "T1", "git_publication", {"branch": "main"})
    ledger.attempts.note_stop("project:P", 7, epoch=1, listed=((OPERATION, ATTEMPT),))
    request = decode_operation_record(
        {
            "record": "stop_readback",
            "task_id": "T1",
            "operation_id": OPERATION,
            "readback": _body("unread", reason="unknown"),
        }
    )
    assert stop_readback_refusal(ledger, request) is None
    assert ledger.target_of(OPERATION) == {"branch": "main"}
    for _ in range(MAX_RECORDS_PER_OPERATION):
        ledger.add_record(OPERATION)
    assert stop_readback_refusal(ledger, request) == "record_limit"


def test_recompute_and_marker_reads_sealed_never_trust_an_unverified_claim() -> None:
    """Only a marker matching the recomputed sealed blob marks an attempt sealed."""
    unknown_obj = decode_stop_readback(_body("sealed", marker_object="0" * 40))
    assert (
        recompute_readback_result(
            unknown_obj, operation_id=OPERATION, intent_sha256=DIGEST, marked=True
        )
        is None
    )
    assert not marker_reads_sealed(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        intent_sha256=None,
        marker_ref=MARKER.ref,
        object_format="sha1",
        marker_object=OBJECTS.sealed,
    )
    assert not marker_reads_sealed(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        intent_sha256=DIGEST,
        marker_ref=MARKER.ref,
        object_format="md5",
        marker_object=OBJECTS.sealed,
    )
    assert not marker_reads_sealed(
        operation_id="",
        attempt_id=ATTEMPT,
        intent_sha256=DIGEST,
        marker_ref=MARKER.ref,
        object_format="sha1",
        marker_object=OBJECTS.sealed,
    )
    assert not marker_reads_sealed(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        intent_sha256=DIGEST,
        marker_ref=MARKER.ref,
        object_format="sha1",
        marker_object=OBJECTS.applied,
    )
    assert marker_reads_sealed(
        operation_id=OPERATION,
        attempt_id=ATTEMPT,
        intent_sha256=DIGEST,
        marker_ref=MARKER.ref,
        object_format="sha1",
        marker_object=OBJECTS.sealed,
    )


def test_duplicate_read_backs_do_not_overwrite_and_contradiction_works_in_both_orders() -> None:
    """A kept closing read-back is returned even at the record limit; late confirmed_done flags."""
    ledger = OperationLedger(None)
    ledger.declare(OPERATION, "T1", "git_publication", {"branch": "main"})
    fact = OperationFact("T1", "P/worker", DIGEST, None, 1.0, "git_publication", "cooperative")
    attempts = ledger.attempts
    attempts.note_declaration(OPERATION, fact)
    attempts.open_attempt(OPERATION, ATTEMPT, executor="P/worker", approval_seq=1)
    attempts.note_outcome(OPERATION, "P/worker", "not_started", seq=5)
    attempts.note_stop("project:P", 7, epoch=1, listed=((OPERATION, ATTEMPT),))
    assert attempts.kept_readback(99, ATTEMPT) is None
    assert attempts.kept_readback(7, ATTEMPT) is None

    # Without marker fields, note_readback("sealed") does NOT mark the attempt sealed.
    attempts.note_readback(8, ATTEMPT, "sealed", seq=6, operation_id=OPERATION)
    assert attempts.attempt_state(OPERATION, ATTEMPT) == "ended"
    other = "b" * 32
    attempts.open_attempt(OPERATION, other, executor="P/worker", approval_seq=2)
    attempts.note_readback(
        7,
        ATTEMPT,
        "unread",
        seq=8,
        operation_id=OPERATION,
    )
    attempts.note_readback(7, ATTEMPT, "unread", seq=9, operation_id=OPERATION)
    assert attempts.kept_readback(7, ATTEMPT) == ("unread", 8)
    attempts.note_readback(
        7,
        ATTEMPT,
        "sealed",
        seq=10,
        operation_id=OPERATION,
        marker_ref=MARKER.ref,
        object_format="sha1",
        marker_object=OBJECTS.sealed,
    )
    assert attempts.attempt_state(OPERATION, ATTEMPT) == "sealed"
    assert attempts.attempt_state(OPERATION, other) == "open"
    attempts.note_readback(7, ATTEMPT, "applied", seq=11, operation_id=OPERATION)
    assert attempts.kept_readback(7, ATTEMPT) == ("sealed", 10)

    # Even when the operation is at MAX_RECORDS_PER_OPERATION, repeating the kept read-back passes.
    for _ in range(MAX_RECORDS_PER_OPERATION):
        ledger.add_record(OPERATION)
    repeat_req = decode_operation_record(
        {
            "record": "stop_readback",
            "task_id": "T1",
            "operation_id": OPERATION,
            "readback": _body("sealed"),
        }
    )
    assert stop_readback_refusal(ledger, repeat_req) is None
    assert kept_stop_readback(ledger, repeat_req) == ("sealed", 10)

    # Multi-attempt: other is open, so confirmed_done ends other without contradicting ATTEMPT.
    attempts.note_outcome(OPERATION, "Q/stranger", "confirmed_done", seq=12)
    attempts.note_outcome(OPERATION, "P/worker", "confirmed_done", seq=13)
    assert attempts.contradicted_at(OPERATION) == 0

    # Order 2: seal lands first on single attempt, then executor claims confirmed_done.
    op2 = "git-op-2"
    attempts.note_declaration(op2, fact)
    attempts.open_attempt(op2, ATTEMPT, executor="P/worker", approval_seq=3)
    attempts.note_seal(op2, ATTEMPT)
    attempts.note_outcome(op2, "P/worker", "confirmed_done", seq=20)
    attempts.note_outcome(op2, "P/worker", "confirmed_done", seq=21)
    assert attempts.contradicted_at(op2) == 20
    m2 = AttemptMarker(op2, ATTEMPT, DIGEST)
    attempts.note_stop("project:P", 25, epoch=2, listed=((op2, ATTEMPT),))
    attempts.note_readback(
        25,
        ATTEMPT,
        "sealed",
        seq=26,
        operation_id=op2,
        marker_ref=m2.ref,
        object_format="sha1",
        marker_object=m2.objects("sha1").sealed,
    )
    assert attempts.contradicted_at(op2) == 20
