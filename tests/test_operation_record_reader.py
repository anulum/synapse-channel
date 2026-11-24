# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit, parity, encapsulation and contract tests for OperationRecordReader
"""Verify OperationRecordReader projections, partitioning, parity and federation exports."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import synapse_channel
from synapse_channel import federation_api
from synapse_channel.core.attempt_markers import AttemptMarker
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    STATE_APPLIED,
    STATE_CLOSED,
    STATE_ENDED,
    STATE_OPEN,
    STATE_RECONCILED,
    STATE_SEALED,
    AttemptLedger,
)
from synapse_channel.core.operation_attempts import APPROVED_FRESH
from synapse_channel.core.operation_record_reader import (
    OPERATION_RECORD_KINDS,
    SCOPE_STOP_RECORD_KINDS,
    IndependentVerificationRecord,
    OperationAttemptSnapshot,
    OperationEvent,
    OperationEvidenceSnapshot,
    OperationMalformedRecord,
    OperationRecordReader,
    OperationRecordSnapshot,
)
from synapse_channel.core.operation_stop_readbacks import (
    RESULT_APPLIED,
    RESULT_SEALED,
    RESULT_UNREAD,
    RESULT_UNSEALABLE,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_reader import EventLogReader

Row = tuple[str, dict[str, Any]]

_DIGEST = "e" * 64
_ATTEMPT_1 = "ab" * 16
_ATTEMPT_2 = "cd" * 16


def _claim(task: str, epoch: int) -> Row:
    return EventKind.CLAIM, {
        "task_id": task,
        "owner": "P/worker",
        "epoch": epoch,
        "ownership_epoch": epoch,
    }


def _declared(task: str, op: str, epoch: int, **extra: Any) -> Row:
    payload: dict[str, Any] = {
        "task_id": task,
        "operation_id": op,
        "sender": "P/worker",
        "effect": "file",
        "snapshot_epoch": epoch,
        "recorded_at": 1_700_000_000.0,
    }
    payload.update(extra)
    return EventKind.OPERATION_DECLARED, payload


def _opened(task: str, op: str, attempt_id: str, **extra: Any) -> Row:
    payload: dict[str, Any] = {
        "task_id": task,
        "operation_id": op,
        "sender": "P/worker",
        "answer": APPROVED_FRESH,
        "attempt_id": attempt_id,
        "approval_seq": 1,
    }
    payload.update(extra)
    return EventKind.OPERATION_ATTEMPT_ANSWERED, payload


def _outcome(task: str, op: str, state: str = "confirmed_done", **extra: Any) -> Row:
    payload: dict[str, Any] = {
        "task_id": task,
        "operation_id": op,
        "sender": "P/worker",
        "state": state,
        "boundary": "worker_process",
    }
    payload.update(extra)
    return EventKind.OPERATION_OUTCOME, payload


def _verified(
    task: str,
    op: str,
    effect: str,
    result: str,
    observation: str = "checked",
    **extra: Any,
) -> Row:
    payload: dict[str, Any] = {
        "task_id": task,
        "operation_id": op,
        "sender": "Q/checker",
        "verifier": "file_change",
        "communication": "confirmed_done",
        "effect": effect,
        "result": result,
        "observation": observation,
        "evidence": {},
    }
    payload.update(extra)
    return EventKind.OPERATION_VERIFIED, payload


def _feed_all(reader: OperationRecordReader, rows: list[Row]) -> None:
    for seq, (kind, payload) in enumerate(rows, start=1):
        reader.feed(kind, seq, payload)


def _write_journal(path: Path, rows: list[Row]) -> None:
    store = EventStore(path)
    try:
        for kind, payload in rows:
            store.append(kind, payload, ts=1_700_000_000.0)
    finally:
        store.close()


def _expected_ledger_state(
    *,
    closed: bool,
    attempt: OperationAttemptSnapshot,
) -> str:
    if closed:
        return STATE_CLOSED
    if attempt.reconciled:
        return STATE_RECONCILED
    if attempt.sealed:
        return STATE_SEALED
    if attempt.applied:
        return STATE_APPLIED
    return STATE_ENDED if attempt.ended else STATE_OPEN


def test_partitioning_refuses_merged_snapshot_and_isolates_tasks() -> None:
    reader = OperationRecordReader()
    _feed_all(
        reader,
        [
            (EventKind.OPERATION_STOPPED, {"scope": "installation", "listed": []}),
            _claim("T-2", 5),
            _declared("T-2", "shared-op", 5, effect="git_publication"),
            _verified("T-2", "shared-op", "observed", "verified", "t2_ok"),
            _claim("T-1", 2),
            _declared("T-1", "shared-op", 2, budget_remaining=0),
            _verified("T-1", "shared-op", "absent", "not_verified", "t1_absent"),
            (EventKind.OPERATION_LEVEL, {"task_id": "T-1", "text": "ignored"}),
            (EventKind.CLAIM, {"task_id": "", "epoch": 9}),
            (EventKind.CLAIM, {"task_id": "T-1", "epoch": 0}),
            (EventKind.CLAIM, {"task_id": "T-1", "epoch": True}),
        ],
    )
    assert reader.task_ids() == ("T-1", "T-2")

    with pytest.raises(ValueError, match="unfiltered"):
        reader.snapshot()
    with pytest.raises(ValueError, match="non-empty string"):
        reader.snapshot("")

    snap_1 = reader.snapshot("T-1")
    snap_2 = reader.snapshot("T-2")
    unseen = reader.snapshot("T-unseen")

    assert snap_1.present is True
    assert snap_1.operations["shared-op"].declared_seq == 6
    assert snap_1.operations["shared-op"].budget == 0
    assert snap_1.operations["shared-op"].absent_seqs == (7,)
    assert snap_1.operations["shared-op"].verified == ()
    assert dict(snap_1.claim_epochs) == {5: 2}
    assert dict(snap_1.standing_stops) == {"installation": 1}

    assert snap_2.present is True
    assert snap_2.operations["shared-op"].declared_seq == 3
    assert snap_2.operations["shared-op"].verified == (
        IndependentVerificationRecord(4, "file_change", "t2_ok"),
    )
    assert dict(snap_2.claim_epochs) == {2: 5}
    assert dict(snap_2.standing_stops) == {"installation": 1}

    assert unseen.present is False
    assert dict(unseen.operations) == {}
    assert dict(unseen.claim_epochs) == {}
    assert dict(unseen.standing_stops) == {"installation": 1}


def test_filtered_reader_tracks_only_its_task_and_validates_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        OperationRecordReader(task_id="")

    reader = OperationRecordReader(task_id="T-1")
    with pytest.raises(ValueError, match="positive integer"):
        reader.feed(EventKind.CLAIM, 0, {"task_id": "T-1", "epoch": 1})
    with pytest.raises(ValueError, match="positive integer"):
        reader.feed(EventKind.CLAIM, True, {"task_id": "T-1", "epoch": 1})

    _feed_all(
        reader,
        [
            _claim("T-2", 4),
            _declared("T-2", "op-other", 4),
            _claim("T-1", 3),
            _declared("T-1", "op-1", 3),
        ],
    )
    assert reader.task_ids() == ("T-1",)
    assert reader.snapshot() == reader.snapshot("T-1")
    assert set(reader.snapshot().operations) == {"op-1"}

    with pytest.raises(ValueError, match="filtered to task"):
        reader.snapshot("T-2")

    empty_filtered = OperationRecordReader(task_id="T-empty")
    assert empty_filtered.snapshot().present is False


def test_unattributed_operation_rows_poison_no_task_and_surface_sorted_seqs() -> None:
    reader = OperationRecordReader()
    _feed_all(
        reader,
        [
            (EventKind.OPERATION_DECLARED, {"operation_id": "op-no-task", "snapshot_epoch": 1}),
            _declared("T-1", "op-1", 2),
            _verified("T-1", "op-1", "observed", "verified", "ok"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "", "operation_id": "op-1", "attempt_id": "a"},
            ),
            (EventKind.OPERATION_OUTCOME, {"task_id": 42, "operation_id": "op-1"}),
        ],
    )
    assert reader.unattributed_seqs() == (1, 4, 5)
    assert reader.task_ids() == ("T-1",)
    snap = reader.snapshot("T-1")
    assert snap.present is True
    assert snap.malformed == ()
    assert list(snap.operations) == ["op-1"]
    assert snap.operations["op-1"].verified == (
        IndependentVerificationRecord(3, "file_change", "ok"),
    )


def test_contradicted_seq_excludes_not_verified_after_close_and_tracks_all_four_pairs() -> None:
    # Verification disagreement (verified + absent / partial) leaves contradicted_seq None.
    disagree = OperationRecordReader(task_id="T")
    _feed_all(
        disagree,
        [
            _declared("T", "op-1", 1),
            _verified("T", "op-1", "observed", "verified", "matched"),
            _verified("T", "op-1", "absent", "not_verified", "gone"),
            _verified("T", "op-1", "observed", "not_verified", "partial"),
        ],
    )
    op = disagree.snapshot().operations["op-1"]
    assert op.contradicted_seq is None
    assert op.verified == (IndependentVerificationRecord(2, "file_change", "matched"),)
    assert op.absent_seqs == (3,)
    assert op.partial_seqs == (4,)

    marker = AttemptMarker("op-git", _ATTEMPT_1, _DIGEST)
    objs = marker.objects("sha256")

    # 1a. sealed -> executor claims confirmed_done (and second contradiction does not overwrite)
    sealed_then_done = OperationRecordReader(task_id="T")
    _feed_all(
        sealed_then_done,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-git", "attempt_id": _ATTEMPT_1},
            ),
            _outcome("T", "op-git", state="confirmed_done"),
            _outcome("T", "op-git", state="unknown", external_effect="confirmed_done"),
        ],
    )
    assert sealed_then_done.snapshot().operations["op-git"].contradicted_seq == 4

    # 1b. executor claims confirmed_done (via non-terminal external_effect) -> sealed
    done_then_sealed = OperationRecordReader(task_id="T")
    _feed_all(
        done_then_sealed,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            _outcome("T", "op-git", state="unknown", external_effect="confirmed_done"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-git", "attempt_id": _ATTEMPT_1},
            ),
        ],
    )
    assert done_then_sealed.snapshot().operations["op-git"].contradicted_seq == 4

    # 2a. applied read-back -> executor claims not_started
    applied_then_none = OperationRecordReader(task_id="T")
    _feed_all(
        applied_then_none,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 3,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": objs.applied,
                },
            ),
            _outcome("T", "op-git", state="not_started"),
        ],
    )
    assert applied_then_none.snapshot().operations["op-git"].contradicted_seq == 5

    # 2b. executor claims not_started -> applied read-back
    none_then_applied = OperationRecordReader(task_id="T")
    _feed_all(
        none_then_applied,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            _outcome("T", "op-git", state="not_started"),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": objs.applied,
                },
            ),
        ],
    )
    assert none_then_applied.snapshot().operations["op-git"].contradicted_seq == 5

    # 3a. matched applied verification blob vs sealed attempt (both orders)
    sealed_vs_verified_applied = OperationRecordReader(task_id="T")
    _feed_all(
        sealed_vs_verified_applied,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-git", "attempt_id": _ATTEMPT_1},
            ),
            _verified(
                "T",
                "op-git",
                "observed",
                "verified",
                "applied_blob",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs.applied},
            ),
        ],
    )
    assert sealed_vs_verified_applied.snapshot().operations["op-git"].contradicted_seq == 4

    verified_applied_vs_readback_sealed = OperationRecordReader(task_id="T")
    _feed_all(
        verified_applied_vs_readback_sealed,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            _verified(
                "T",
                "op-git",
                "observed",
                "verified",
                "applied_blob",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs.applied},
            ),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_SEALED,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": objs.sealed,
                },
            ),
        ],
    )
    assert verified_applied_vs_readback_sealed.snapshot().operations["op-git"].contradicted_seq == 5

    # 3b. matched sealed verification blob vs applied read-back (both orders)
    applied_vs_verified_sealed = OperationRecordReader(task_id="T")
    _feed_all(
        applied_vs_verified_sealed,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 3,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": objs.applied,
                },
            ),
            _verified(
                "T",
                "op-git",
                "absent",
                "not_verified",
                "sealed_blob",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs.sealed},
            ),
        ],
    )
    assert applied_vs_verified_sealed.snapshot().operations["op-git"].contradicted_seq == 5

    verified_sealed_vs_readback_applied = OperationRecordReader(task_id="T")
    _feed_all(
        verified_sealed_vs_readback_applied,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            _verified(
                "T",
                "op-git",
                "absent",
                "not_verified",
                "sealed_blob",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs.sealed},
            ),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": objs.applied,
                },
            ),
        ],
    )
    assert verified_sealed_vs_readback_applied.snapshot().operations["op-git"].contradicted_seq == 5


def test_unsealable_readback_blocks_both_sealed_and_applied_and_pins_ledger_divergence(
    tmp_path: Path,
) -> None:
    marker = AttemptMarker("op-git", _ATTEMPT_1, _DIGEST)
    objs = marker.objects("sha256")
    for closing_result, blob in ((RESULT_SEALED, objs.sealed), (RESULT_APPLIED, objs.applied)):
        rows: list[Row] = [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-git", _ATTEMPT_1),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-git",
                    "epoch": 1,
                    "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 3,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_UNSEALABLE,
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "stop_seq": 3,
                    "attempt_id": _ATTEMPT_1,
                    "result": closing_result,
                    "marker_ref": marker.ref,
                    "object_format": "sha256",
                    "marker_object": blob,
                },
            ),
        ]
        db = tmp_path / f"div_{closing_result}.db"
        _write_journal(db, rows)

        reader = OperationRecordReader(task_id="T")
        with EventLogReader(db) as log_reader:
            for event in log_reader.iter_events():
                reader.feed_event(event)

        attempt = reader.snapshot().operations["op-git"].attempts[0]
        assert attempt.sealed is False
        assert attempt.applied is False
        assert attempt.ended is False
        assert reader.snapshot().operations["op-git"].applied_readback_seqs == ()

        # Pin the unchanged live AttemptLedger behaviour side by side: StopLedger keeps
        # unsealable, while AttemptLedger._mark_attempt_from_readback still marks the attempt.
        store = EventStore(db)
        try:
            ledger = AttemptLedger(store)
        finally:
            store.close()
        assert ledger.readback_of(3, _ATTEMPT_1) == RESULT_UNSEALABLE
        expected_hub_state = STATE_SEALED if closing_result == RESULT_SEALED else STATE_APPLIED
        assert ledger.attempt_state("op-git", _ATTEMPT_1) == expected_hub_state


def test_verification_marker_matching_outcomes_and_reconciliation_edges() -> None:
    marker_1 = AttemptMarker("op-git", _ATTEMPT_1, _DIGEST)
    marker_2 = AttemptMarker("op-git", _ATTEMPT_2, _DIGEST)
    objs_1_sha1 = marker_1.objects("sha1")
    objs_2_sha256 = marker_2.objects("sha256")

    reader = OperationRecordReader(task_id="T")
    _feed_all(
        reader,
        [
            _declared("T", "op-git", 1, effect="git_publication", intent_sha256=_DIGEST),
            # Duplicate declaration changes nothing.
            _declared("T", "op-git", 9, sender="OTHER/late"),
            # Non-approved attempt answer opens nothing and is not malformed.
            (
                EventKind.OPERATION_ATTEMPT_ANSWERED,
                {
                    "task_id": "T",
                    "operation_id": "op-git",
                    "sender": "P/worker",
                    "answer": "stopped",
                },
            ),
            _opened("T", "op-git", _ATTEMPT_1),
            _opened("T", "op-git", _ATTEMPT_2, sender="P/other"),
            # Missing-sender outcome and non-executor outcome end nothing.
            (EventKind.OPERATION_OUTCOME, {"task_id": "T", "operation_id": "op-git"}),
            _outcome("T", "op-git", sender="Z/stranger", state="confirmed_failed"),
            # Declarer self-verification is ignored for independent fields and reconciliation.
            _verified(
                "T",
                "op-git",
                "observed",
                "verified",
                "",
                sender="P/worker",
                verifier="",
            ),
            # Pre-end matched sha1 verification (pre-0.99.59 shape, no attempt_id)
            # binds only attempt 1.
            _verified(
                "T",
                "op-git",
                "absent",
                "not_verified",
                "",
                verifier="",
                evidence={"marker_object": objs_1_sha1.sealed},
            ),
            # Mismatched marker (sealed blob paired with observed) reconciles nothing.
            _verified(
                "T",
                "op-git",
                "observed",
                "not_verified",
                "mismatch",
                evidence={"attempt_id": _ATTEMPT_2, "marker_object": objs_2_sha256.sealed},
            ),
            # Unknown effect verification records unknown_seqs and default labels.
            _verified("T", "op-git", "unknown", "not_checked", "", verifier=""),
            # Ending attempt 1 reconciles it immediately via earlier pre-end marker_observed.
            _outcome("T", "op-git", state="confirmed_failed"),
            # Ending attempt 2 without matching marker leaves it unreconciled until unscoped check.
            _outcome("T", "op-git", sender="P/other", state="not_started"),
            # Unscoped absent verification reconciles ended attempt 2.
            _verified("T", "op-git", "absent", "not_verified", "unscoped", evidence="not_a_map"),
        ],
    )
    snap = reader.snapshot()
    op = snap.operations["op-git"]
    assert op.declared_seq == 1
    assert op.declarer == "P/worker"
    assert op.verified == ()
    assert op.partial_seqs == (10,)
    assert op.absent_seqs == (9, 14)
    assert op.unknown_seqs == (11,)
    assert op.checked is True
    assert op.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 4, True, False, False, True),
        OperationAttemptSnapshot(_ATTEMPT_2, "P/other", 5, True, False, False, True),
    )

    # Unscoped verification leaves an open attempt unreconciled; default labels apply.
    open_reader = OperationRecordReader(task_id="T")
    _feed_all(
        open_reader,
        [
            _declared("T", "op-open", 1),
            _opened("T", "op-open", _ATTEMPT_1),
            _verified("T", "op-open", "absent", "not_verified", "pre"),
            _verified("T", "op-open", "observed", "verified", "", verifier=""),
        ],
    )
    open_op = open_reader.snapshot().operations["op-open"]
    assert open_op.attempts[0].reconciled is False
    assert open_op.verified == (IndependentVerificationRecord(4, "a verifier", "no observation"),)
    # Seal of non-matching attempt leaves open attempt open; unread after applied keeps applied.
    marker_open = AttemptMarker("op-open", _ATTEMPT_1, _DIGEST)
    objs_open = marker_open.objects("sha256")
    rb_reader = OperationRecordReader(task_id="T")
    _feed_all(
        rb_reader,
        [
            _declared("T", "op-open", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-open", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-open", "attempt_id": _ATTEMPT_2},
            ),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-open",
                    "listed": [{"operation_id": "op-open", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-open",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker_open.ref,
                    "object_format": "sha256",
                    "marker_object": objs_open.applied,
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-open",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_UNREAD,
                },
            ),
        ],
    )
    rb_op = rb_reader.snapshot().operations["op-open"]
    assert rb_op.applied_readback_seqs == (5,)
    assert rb_op.attempts[0].applied is True
    assert rb_op.attempts[0].sealed is False

    # A sealed attempt is not flipped to applied by a later applied read-back.
    sealed_first = OperationRecordReader(task_id="T")
    _feed_all(
        sealed_first,
        [
            _declared("T", "op-open", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-open", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-open", "attempt_id": _ATTEMPT_1},
            ),
            (
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "operation:op-open",
                    "listed": [{"operation_id": "op-open", "attempt_id": _ATTEMPT_1}],
                },
            ),
            (
                EventKind.OPERATION_STOP_READBACK,
                {
                    "task_id": "T",
                    "operation_id": "op-open",
                    "stop_seq": 4,
                    "attempt_id": _ATTEMPT_1,
                    "result": RESULT_APPLIED,
                    "marker_ref": marker_open.ref,
                    "object_format": "sha256",
                    "marker_object": objs_open.applied,
                },
            ),
        ],
    )
    sealed_first_op = sealed_first.snapshot().operations["op-open"]
    assert sealed_first_op.attempts[0].sealed is True
    assert sealed_first_op.attempts[0].applied is False
    assert sealed_first_op.applied_readback_seqs == (5,)


def test_executor_outcome_applies_to_latest_attempt_not_first() -> None:
    """An executor's outcome attaches to its latest attempt, not its first."""
    # First attempt sealed, second open -> confirmed_done lands on second (no contradiction).
    retry_after_seal = OperationRecordReader(task_id="T")
    _feed_all(
        retry_after_seal,
        [
            _declared("T", "op-retry", 1),
            _opened("T", "op-retry", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-retry", "attempt_id": _ATTEMPT_1},
            ),
            _opened("T", "op-retry", _ATTEMPT_2),
            _outcome("T", "op-retry", state="confirmed_done"),
        ],
    )
    assert retry_after_seal.snapshot().operations["op-retry"].contradicted_seq is None

    # First attempt open, second attempt sealed -> confirmed_done contradicts the sealed second.
    second_sealed = OperationRecordReader(task_id="T")
    _feed_all(
        second_sealed,
        [
            _declared("T", "op-second-sealed", 1),
            _opened("T", "op-second-sealed", _ATTEMPT_1),
            _opened("T", "op-second-sealed", _ATTEMPT_2),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-second-sealed", "attempt_id": _ATTEMPT_2},
            ),
            _outcome("T", "op-second-sealed", state="confirmed_done"),
        ],
    )
    assert second_sealed.snapshot().operations["op-second-sealed"].contradicted_seq == 5

    # Two open attempts by the same executor, then confirmed_done marks only the second attempt.
    seal_second = OperationRecordReader(task_id="T")
    _feed_all(
        seal_second,
        [
            _declared("T", "op-mark-latest", 1),
            _opened("T", "op-mark-latest", _ATTEMPT_1),
            _opened("T", "op-mark-latest", _ATTEMPT_2),
            _outcome("T", "op-mark-latest", state="unknown", external_effect="confirmed_done"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-mark-latest", "attempt_id": _ATTEMPT_2},
            ),
        ],
    )
    assert seal_second.snapshot().operations["op-mark-latest"].contradicted_seq == 5

    seal_first = OperationRecordReader(task_id="T")
    _feed_all(
        seal_first,
        [
            _declared("T", "op-mark-latest", 1),
            _opened("T", "op-mark-latest", _ATTEMPT_1),
            _opened("T", "op-mark-latest", _ATTEMPT_2),
            _outcome("T", "op-mark-latest", state="unknown", external_effect="confirmed_done"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-mark-latest", "attempt_id": _ATTEMPT_1},
            ),
        ],
    )
    assert seal_first.snapshot().operations["op-mark-latest"].contradicted_seq is None


def test_non_fresh_attempt_answers_open_no_attempt_and_record_no_malformed_row() -> None:
    """Refusals and other non-fresh attempt answers open no attempt and are not malformed."""
    reader = OperationRecordReader(task_id="T")
    _feed_all(
        reader,
        [
            _declared("T", "op-refused", 1),
            (
                EventKind.OPERATION_ATTEMPT_ANSWERED,
                {
                    "task_id": "T",
                    "operation_id": "op-refused",
                    "sender": "P/worker",
                    "answer": "refused",
                    "attempt_id": _ATTEMPT_1,
                },
            ),
            (
                EventKind.OPERATION_ATTEMPT_ANSWERED,
                {
                    "task_id": "T",
                    "operation_id": "op-refused",
                    "sender": "P/worker",
                    "answer": "stopped",
                },
            ),
            (
                EventKind.OPERATION_ATTEMPT_ANSWERED,
                {
                    "task_id": "T",
                    "operation_id": "op-refused",
                    "sender": "P/worker",
                    "answer": "already_verified",
                    "attempt_id": _ATTEMPT_2,
                },
            ),
        ],
    )
    snap = reader.snapshot()
    assert snap.malformed == ()
    assert snap.operations["op-refused"].attempts == ()


def test_seal_after_ended_attempt_does_not_mark_sealed_or_contradict() -> None:
    """A seal naming an already-ended attempt leaves it unsealed and uncontradicted."""
    done_then_seal = OperationRecordReader(task_id="T")
    _feed_all(
        done_then_seal,
        [
            _declared("T", "op-ended", 1),
            _opened("T", "op-ended", _ATTEMPT_1),
            _outcome("T", "op-ended", state="confirmed_done"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-ended", "attempt_id": _ATTEMPT_1},
            ),
        ],
    )
    snap_done = done_then_seal.snapshot()
    op_done = snap_done.operations["op-ended"]
    assert snap_done.malformed == ()
    assert op_done.contradicted_seq is None
    assert op_done.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 2, True, False, False, False),
    )

    failed_then_seal = OperationRecordReader(task_id="T")
    _feed_all(
        failed_then_seal,
        [
            _declared("T", "op-failed", 1),
            _opened("T", "op-failed", _ATTEMPT_1),
            _outcome("T", "op-failed", state="confirmed_failed"),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-failed", "attempt_id": _ATTEMPT_1},
            ),
        ],
    )
    op_failed = failed_then_seal.snapshot().operations["op-failed"]
    assert op_failed.contradicted_seq is None
    assert op_failed.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 2, True, False, False, False),
    )


def test_declarer_self_verification_never_reconciles_and_independent_verifier_does() -> None:
    """Only a sender distinct from a known declarer can reconcile attempts."""
    marker_1 = AttemptMarker("op-indep", _ATTEMPT_1, _DIGEST)
    objs_1 = marker_1.objects("sha256")
    reader = OperationRecordReader(task_id="T")
    _feed_all(
        reader,
        [
            _declared("T", "op-indep", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-indep", _ATTEMPT_1),
            _outcome("T", "op-indep", state="confirmed_failed"),
            _verified("T", "op-indep", "observed", "verified", "self_unscoped", sender="P/worker"),
            _verified(
                "T",
                "op-indep",
                "observed",
                "verified",
                "self_marker",
                sender="P/worker",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs_1.applied},
            ),
        ],
    )
    before = reader.snapshot().operations["op-indep"]
    assert before.verified == ()
    assert before.checked is False
    assert before.contradicted_seq is None
    assert before.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 2, True, False, False, False),
    )

    # An independent sender reconciles the ended attempt and records the verification.
    indep_kind, indep_payload = _verified(
        "T", "op-indep", "observed", "verified", "independent_ok", sender="Q/checker"
    )
    reader.feed(indep_kind, 6, indep_payload)
    after = reader.snapshot().operations["op-indep"]
    assert after.checked is True
    assert after.verified == (IndependentVerificationRecord(6, "file_change", "independent_ok"),)
    assert after.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 2, True, False, False, True),
    )

    # Declarer self-verification with an opposing marker blob does not contradict a sealed attempt.
    marker_2 = AttemptMarker("op-self-blob", _ATTEMPT_1, _DIGEST)
    objs_2 = marker_2.objects("sha256")
    sealed_self = OperationRecordReader(task_id="T")
    _feed_all(
        sealed_self,
        [
            _declared("T", "op-self-blob", 1, effect="git_publication", intent_sha256=_DIGEST),
            _opened("T", "op-self-blob", _ATTEMPT_1),
            (
                EventKind.OPERATION_SEALED,
                {"task_id": "T", "operation_id": "op-self-blob", "attempt_id": _ATTEMPT_1},
            ),
            _verified(
                "T",
                "op-self-blob",
                "observed",
                "verified",
                "self_applied",
                sender="P/worker",
                evidence={"attempt_id": _ATTEMPT_1, "marker_object": objs_2.applied},
            ),
        ],
    )
    sealed_op = sealed_self.snapshot().operations["op-self-blob"]
    assert sealed_op.verified == ()
    assert sealed_op.contradicted_seq is None
    assert sealed_op.attempts[0].sealed is True
    assert sealed_op.attempts[0].reconciled is False

    # Without a declaration establishing declarer, verification cannot establish independence.
    undeclared = OperationRecordReader(task_id="T")
    _feed_all(
        undeclared,
        [
            _opened("T", "op-undeclared", _ATTEMPT_1),
            _outcome("T", "op-undeclared", state="confirmed_failed"),
            _verified("T", "op-undeclared", "observed", "verified", "no_decl", sender="Q/checker"),
        ],
    )
    undeclared_op = undeclared.snapshot().operations["op-undeclared"]
    assert undeclared_op.declarer is None
    assert undeclared_op.attempts == (
        OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 1, True, False, False, False),
    )


@pytest.mark.parametrize(
    ("rows", "expected_detail", "scope_level"),
    [
        (
            [(EventKind.OPERATION_DECLARED, {"task_id": "T", "operation_id": ""})],
            "names no operation",
            False,
        ),
        (
            [
                (
                    EventKind.OPERATION_DECLARED,
                    {"task_id": "T", "operation_id": "op", "snapshot_epoch": "3"},
                )
            ],
            "no usable snapshot epoch",
            False,
        ),
        (
            [
                (
                    EventKind.OPERATION_DECLARED,
                    {
                        "task_id": "T",
                        "operation_id": "op",
                        "snapshot_epoch": 3,
                        "budget_remaining": -1,
                    },
                )
            ],
            "an unusable budget",
            False,
        ),
        (
            [
                (
                    EventKind.OPERATION_DECLARED,
                    {"task_id": "T", "operation_id": "op", "snapshot_epoch": 3},
                )
            ],
            "no usable sender",
            False,
        ),
        ([_verified("T", "op", "absent", "verified", "x")], "unknown effect or result", False),
        ([_verified("T", "op", "unknown", "verified", "x")], "unknown effect or result", False),
        (
            [
                _declared("T", "op", 1),
                (
                    EventKind.OPERATION_VERIFIED,
                    {
                        "task_id": "T",
                        "operation_id": "op",
                        "effect": "absent",
                        "result": "not_verified",
                    },
                ),
            ],
            "no usable sender",
            False,
        ),
        (
            [
                _declared("T", "op", 1),
                (EventKind.OPERATION_ATTEMPT_ANSWERED, {"task_id": "T", "operation_id": "op"}),
            ],
            "no usable answer",
            False,
        ),
        (
            [
                _declared("T", "op", 1),
                (
                    EventKind.OPERATION_ATTEMPT_ANSWERED,
                    {
                        "task_id": "T",
                        "operation_id": "op",
                        "answer": APPROVED_FRESH,
                        "sender": "P/w",
                    },
                ),
            ],
            "lacks sender or attempt_id",
            False,
        ),
        (
            [
                _declared("T", "op", 1),
                (EventKind.OPERATION_SEALED, {"task_id": "T", "operation_id": "op"}),
            ],
            "names no attempt",
            False,
        ),
        (
            [
                _declared("T", "op", 1),
                (
                    EventKind.OPERATION_STOP_READBACK,
                    {"task_id": "T", "operation_id": "op", "stop_seq": True},
                ),
            ],
            "unusable stop, attempt or result",
            False,
        ),
        (
            [
                _declared("T", "op", 1),
                (
                    EventKind.OPERATION_STOP_READBACK,
                    {
                        "task_id": "T",
                        "operation_id": "op",
                        "stop_seq": 1,
                        "attempt_id": _ATTEMPT_1,
                        "result": RESULT_UNREAD,
                    },
                ),
            ],
            "did not list",
            False,
        ),
        (
            [
                _declared("T", "op-git", 1, intent_sha256=_DIGEST),
                (
                    EventKind.OPERATION_STOPPED,
                    {
                        "scope": "installation",
                        "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                    },
                ),
                (
                    EventKind.OPERATION_STOP_READBACK,
                    {
                        "task_id": "T",
                        "operation_id": "op-git",
                        "stop_seq": 2,
                        "attempt_id": _ATTEMPT_1,
                        "result": RESULT_SEALED,
                    },
                ),
            ],
            "lacks marker fields",
            False,
        ),
        (
            [
                _declared("T", "op-git", 1, intent_sha256=_DIGEST),
                (
                    EventKind.OPERATION_STOPPED,
                    {
                        "scope": "installation",
                        "listed": [{"operation_id": "op-git", "attempt_id": _ATTEMPT_1}],
                    },
                ),
                (
                    EventKind.OPERATION_STOP_READBACK,
                    {
                        "task_id": "T",
                        "operation_id": "op-git",
                        "stop_seq": 2,
                        "attempt_id": _ATTEMPT_1,
                        "result": RESULT_SEALED,
                        "marker_ref": AttemptMarker("op-git", _ATTEMPT_1, _DIGEST).ref,
                        "object_format": "sha256",
                        "marker_object": "0" * 64,
                    },
                ),
            ],
            "does not match",
            False,
        ),
        ([(EventKind.OPERATION_STOPPED, {"scope": ""})], "names no scope", True),
        (
            [(EventKind.OPERATION_STOPPED, {"scope": "installation", "listed": "bad"})],
            "unusable listed field",
            True,
        ),
        (
            [(EventKind.OPERATION_STOPPED, {"scope": "installation", "listed": ["bad"]})],
            "malformed listed entry",
            True,
        ),
        (
            [
                (
                    EventKind.OPERATION_STOPPED,
                    {"scope": "installation", "listed": [{"operation_id": "op"}]},
                )
            ],
            "incomplete listed entry",
            True,
        ),
    ],
)
def test_every_malformed_shape_fails_closed_and_scope_malformed_alone_is_not_present(
    rows: list[Row], expected_detail: str, scope_level: bool
) -> None:
    reader = OperationRecordReader(task_id="T")
    _feed_all(reader, rows)
    snap = reader.snapshot()
    assert len(snap.malformed) == 1
    record = snap.malformed[0]
    assert expected_detail in record.detail
    assert record.scope_level is scope_level
    assert snap.present is (not scope_level)


def test_parity_against_fleet_collector_field_by_field_via_read_only_log_reader(
    tmp_path: Path,
) -> None:
    """Replay a multi-scenario journal through EventLogReader and assert every snapshot field.

    The expected values are what the fleet tier's evidence collector produces for this journal;
    the reader's one deliberate difference, an ``unsealable`` read-back followed by a ``sealed``
    one, is pinned in the divergence test.
    """
    marker_1 = AttemptMarker("op-git", _ATTEMPT_1, _DIGEST)
    objs_1 = marker_1.objects("sha256")
    rows: list[Row] = [
        _claim("T", 3),
        _declared(
            "T", "op-git", 3, effect="git_publication", intent_sha256=_DIGEST, budget_remaining=2
        ),
        _opened("T", "op-git", _ATTEMPT_1),
        _opened("T", "op-git", _ATTEMPT_2, sender="P/other"),
        _outcome("T", "op-git", state="confirmed_failed"),
        _outcome("T", "op-git", sender="P/other", state="not_started"),
        (
            EventKind.OPERATION_STOPPED,
            {
                "scope": "effect:git_publication",
                "epoch": 1,
                "listed": [
                    {"operation_id": "op-git", "attempt_id": _ATTEMPT_1},
                    {"operation_id": "op-git", "attempt_id": _ATTEMPT_2},
                ],
            },
        ),
        (
            EventKind.OPERATION_STOP_READBACK,
            {
                "task_id": "T",
                "operation_id": "op-git",
                "stop_seq": 7,
                "attempt_id": _ATTEMPT_1,
                "result": RESULT_UNREAD,
            },
        ),
        (
            EventKind.OPERATION_STOP_READBACK,
            {
                "task_id": "T",
                "operation_id": "op-git",
                "stop_seq": 7,
                "attempt_id": _ATTEMPT_1,
                "result": RESULT_SEALED,
                "marker_ref": marker_1.ref,
                "object_format": "sha256",
                "marker_object": objs_1.sealed,
            },
        ),
        _verified("T", "op-git", "absent", "not_verified", "file_absent"),
        (EventKind.OPERATION_RESUMED, {"scope": "effect:git_publication"}),
        (EventKind.OPERATION_STOPPED, {"scope": "installation", "epoch": 1, "listed": []}),
        _declared("T", "op-done", 3, budget_remaining=0),
        _verified("T", "op-done", "observed", "verified", "content_matches"),
        _claim("T", 4),
    ]
    db = tmp_path / "fleet_parity.db"
    _write_journal(db, rows)

    reader = OperationRecordReader()
    with EventLogReader(db) as log_reader:
        for event in log_reader.iter_events():
            reader.feed_event(event)

    snap = reader.snapshot("T")
    assert snap.present is True
    assert snap.malformed == ()
    assert dict(snap.claim_epochs) == {1: 3, 15: 4}
    assert dict(snap.standing_stops) == {"installation": 12}

    op_git = snap.operations["op-git"]
    assert op_git == OperationRecordSnapshot(
        declared_seq=2,
        snapshot_epoch=3,
        budget=2,
        declarer="P/worker",
        effect="git_publication",
        intent_sha256=_DIGEST,
        scopes=("operation:op-git", "effect:git_publication", "project:P", "installation"),
        attempts=(
            OperationAttemptSnapshot(_ATTEMPT_1, "P/worker", 3, True, True, False, True),
            OperationAttemptSnapshot(_ATTEMPT_2, "P/other", 4, True, False, False, True),
        ),
        applied_readback_seqs=(),
        contradicted_seq=None,
        verified=(),
        partial_seqs=(),
        absent_seqs=(10,),
        unknown_seqs=(),
    )
    assert op_git.checked is True

    op_done = snap.operations["op-done"]
    assert op_done == OperationRecordSnapshot(
        declared_seq=13,
        snapshot_epoch=3,
        budget=0,
        declarer="P/worker",
        effect="file",
        intent_sha256=None,
        scopes=("operation:op-done", "effect:file", "project:P", "installation"),
        attempts=(),
        applied_readback_seqs=(),
        contradicted_seq=None,
        verified=(IndependentVerificationRecord(14, "file_change", "content_matches"),),
        partial_seqs=(),
        absent_seqs=(),
        unknown_seqs=(),
    )
    assert op_done.checked is True


def test_parity_against_attempt_ledger_on_shared_facts(tmp_path: Path) -> None:
    """Reader and AttemptLedger agree on sealed, applied, reconciled, ended and stops."""
    marker_a = AttemptMarker("op-a", _ATTEMPT_1, _DIGEST)
    marker_b = AttemptMarker("op-b", _ATTEMPT_2, _DIGEST)
    objs_a = marker_a.objects("sha256")
    objs_b = marker_b.objects("sha256")

    rows: list[Row] = [
        _declared("T", "op-a", 1, effect="git_publication", intent_sha256=_DIGEST),
        _declared("T", "op-b", 1, effect="git_publication", intent_sha256=_DIGEST),
        _declared("T", "op-c", 1, effect="file"),
        _opened("T", "op-a", _ATTEMPT_1),
        _opened("T", "op-b", _ATTEMPT_2),
        _opened("T", "op-c", "ef" * 16),
        (
            EventKind.OPERATION_STOPPED,
            {
                "scope": "effect:git_publication",
                "epoch": 1,
                "listed": [
                    {"operation_id": "op-a", "attempt_id": _ATTEMPT_1},
                    {"operation_id": "op-b", "attempt_id": _ATTEMPT_2},
                ],
            },
        ),
        (
            EventKind.OPERATION_STOP_READBACK,
            {
                "task_id": "T",
                "operation_id": "op-a",
                "stop_seq": 7,
                "attempt_id": _ATTEMPT_1,
                "result": RESULT_SEALED,
                "marker_ref": marker_a.ref,
                "object_format": "sha256",
                "marker_object": objs_a.sealed,
            },
        ),
        (
            EventKind.OPERATION_STOP_READBACK,
            {
                "task_id": "T",
                "operation_id": "op-b",
                "stop_seq": 7,
                "attempt_id": _ATTEMPT_2,
                "result": RESULT_APPLIED,
                "marker_ref": marker_b.ref,
                "object_format": "sha256",
                "marker_object": objs_b.applied,
            },
        ),
        _outcome("T", "op-c", state="confirmed_failed"),
        _verified("T", "op-c", "absent", "not_verified", "reconciled"),
        (EventKind.OPERATION_STOPPED, {"scope": "installation", "epoch": 1, "listed": []}),
        (EventKind.OPERATION_RESUMED, {"scope": "effect:git_publication"}),
    ]
    db = tmp_path / "ledger_parity.db"
    _write_journal(db, rows)

    reader = OperationRecordReader(task_id="T")
    with EventLogReader(db) as log_reader:
        for event in log_reader.iter_events():
            reader.feed_event(event)
    snap = reader.snapshot()

    store = EventStore(db)
    try:
        ledger = AttemptLedger(store)
    finally:
        store.close()

    for op_id, op_snap in snap.operations.items():
        assert op_snap.scopes == ledger.operation_scopes(op_id)
        closed = bool(op_snap.verified)
        for attempt in op_snap.attempts:
            assert ledger.reconciled(op_id, attempt.attempt_id) is (closed or attempt.reconciled)
            assert ledger.attempt_state(op_id, attempt.attempt_id) == _expected_ledger_state(
                closed=closed, attempt=attempt
            )

    for scope in ("installation", "effect:git_publication", "project:P", "operation:op-a"):
        standing = ledger.standing_stop(scope)
        assert snap.standing_stops.get(scope) == (None if standing is None else standing[0])


def test_immutability_encapsulation_public_signatures_and_strict_mypy_consumer(
    tmp_path: Path,
) -> None:
    reader = OperationRecordReader(task_id="T")
    _feed_all(reader, [_claim("T", 1), _declared("T", "op-1", 1)])
    snap = reader.snapshot()

    mutable_snap: Any = snap
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_snap.malformed = ()
    ops_map: Any = snap.operations
    with pytest.raises(TypeError):
        ops_map["x"] = snap.operations["op-1"]
    stops_map: Any = snap.standing_stops
    with pytest.raises(TypeError):
        stops_map["installation"] = 99
    claims_map: Any = snap.claim_epochs
    with pytest.raises(TypeError):
        claims_map[99] = 99

    # Encapsulation: neither AttemptLedger nor StopLedger is exported or reachable publicly.
    assert not hasattr(federation_api, "AttemptLedger")
    assert not hasattr(federation_api, "StopLedger")
    for name in dir(reader):
        if name.startswith("_"):
            continue
        assert not isinstance(getattr(reader, name), AttemptLedger)

    # Every non-builtin type referenced in an exported signature is itself exported.
    source_path = (
        Path(synapse_channel.__file__).resolve().parent / "core" / "operation_record_reader.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    exported = set(federation_api.__all__)
    allowed_names = exported | {
        "Any",
        "Mapping",
        "None",
        "bool",
        "frozenset",
        "int",
        "str",
        "tuple",
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in exported:
            for member in node.body:
                if isinstance(member, ast.AnnAssign) and member.annotation is not None:
                    refs = {n.id for n in ast.walk(member.annotation) if isinstance(n, ast.Name)}
                    assert refs <= allowed_names, (
                        f"{node.name} leaked private type: {refs - allowed_names}"
                    )
                elif isinstance(member, ast.FunctionDef) and not (
                    member.name.startswith("_") and member.name != "__init__"
                ):
                    annotations = [
                        arg.annotation
                        for arg in (*member.args.args, *member.args.kwonlyargs)
                        if arg.annotation is not None
                    ]
                    if member.returns is not None:
                        annotations.append(member.returns)
                    for ann in annotations:
                        refs = {n.id for n in ast.walk(ann) if isinstance(n, ast.Name)}
                        assert refs <= allowed_names, (
                            f"{node.name}.{member.name} leaked private type: {refs - allowed_names}"
                        )

    # Exact federation_api object identity for all 9 symbols.
    expected_objects = {
        "IndependentVerificationRecord": IndependentVerificationRecord,
        "OPERATION_RECORD_KINDS": OPERATION_RECORD_KINDS,
        "OperationAttemptSnapshot": OperationAttemptSnapshot,
        "OperationEvent": OperationEvent,
        "OperationEvidenceSnapshot": OperationEvidenceSnapshot,
        "OperationMalformedRecord": OperationMalformedRecord,
        "OperationRecordReader": OperationRecordReader,
        "OperationRecordSnapshot": OperationRecordSnapshot,
        "SCOPE_STOP_RECORD_KINDS": SCOPE_STOP_RECORD_KINDS,
    }
    for name, obj in expected_objects.items():
        assert getattr(federation_api, name) is obj
    assert inspect.isclass(OperationEvent)

    # Strict mypy probe from an external consumer importing via federation_api.
    consumer = tmp_path / "ops_consumer.py"
    consumer.write_text(
        "\n".join(
            (
                "from dataclasses import dataclass",
                "from typing import Any",
                "from synapse_channel.federation_api import (",
                "    OperationEvent,",
                "    OperationEvidenceSnapshot,",
                "    OperationRecordReader,",
                "    StoredEvent,",
                ")",
                "",
                "@dataclass(frozen=True)",
                "class CustomHubEvent:",
                "    seq: int",
                "    kind: str",
                "    payload: dict[str, Any]",
                "",
                "def project(",
                "    stored: StoredEvent, hub: CustomHubEvent",
                ") -> OperationEvidenceSnapshot:",
                "    reader = OperationRecordReader()",
                "    ev: OperationEvent = hub",
                "    reader.feed_event(stored)",
                "    reader.feed_event(ev)",
                "    _ = reader.task_ids()",
                "    _ = reader.unattributed_seqs()",
                "    return reader.snapshot('T-1')",
                "",
            )
        ),
        encoding="utf-8",
    )
    package_root = Path(synapse_channel.__file__).resolve().parents[1]
    probe = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        check=False,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        text=True,
        timeout=180,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
