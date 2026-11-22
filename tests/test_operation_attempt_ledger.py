# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the ledger of attempts: who ends, who observes, and the restore
"""Only the executor ends its attempt; only another sender observes or closes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.attempt_markers import AttemptMarker
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    ATTEMPT_STATES,
    STATE_APPLIED,
    STATE_CLOSED,
    STATE_ENDED,
    STATE_NONE,
    STATE_OPEN,
    STATE_RECONCILED,
    STATE_SEALED,
    AttemptLedger,
    AttemptView,
)
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_stop_states import STATE_AT_REST
from synapse_channel.core.persistence import EventStore

WORKER = "P/worker"
CHECKER = "Q/checker"
FACT = OperationFact("T1", WORKER, "d" * 64, 2_000_000_000, 1_800_000_000.0)


def _ledger_with_open_attempt() -> AttemptLedger:
    ledger = AttemptLedger(None)
    ledger.note_declaration("op-1", FACT)
    ledger.open_attempt("op-1", "a1", executor=WORKER, approval_seq=7)
    return ledger


def test_a_declaration_stands_once_and_an_unknown_operation_has_nothing() -> None:
    ledger = AttemptLedger(None)
    assert ledger.operation("op-1") is None
    assert ledger.view("op-1", 7) == AttemptView(False, False, False, False)
    assert ledger.attempt_state("op-1", "a1") == STATE_NONE
    ledger.open_attempt("op-1", "a1", executor=WORKER, approval_seq=7)
    ledger.note_outcome("op-1", WORKER, "confirmed_done")
    ledger.note_verification("op-1", CHECKER, effect="observed", result="verified")
    assert ledger.operations == {}
    ledger.note_declaration("op-1", FACT)
    ledger.note_declaration("op-1", OperationFact("T9", "P/other", None, None, 0.0))
    assert ledger.operation("op-1") == FACT


def test_an_open_attempt_consumes_its_approval_and_names_its_state() -> None:
    ledger = _ledger_with_open_attempt()
    assert ledger.view("op-1", 7) == AttemptView(False, True, True, False)
    assert ledger.view("op-1", 8) == AttemptView(False, True, False, False)
    assert ledger.attempt_state("op-1", "a1") == STATE_OPEN
    assert ledger.attempt_state("op-1", "a2") == STATE_NONE


def test_only_the_executors_terminal_outcome_ends_its_attempt() -> None:
    ledger = _ledger_with_open_attempt()
    ledger.note_outcome("op-1", CHECKER, "confirmed_done")
    ledger.note_outcome("op-1", WORKER, "started")
    ledger.note_outcome("op-1", WORKER, "unknown")
    assert ledger.attempt_state("op-1", "a1") == STATE_OPEN
    ledger.note_outcome("op-1", WORKER, "not_started")
    assert ledger.attempt_state("op-1", "a1") == STATE_ENDED
    assert ledger.view("op-1", 7) == AttemptView(False, False, True, True)


def test_the_declarers_own_verification_counts_for_nothing() -> None:
    ledger = _ledger_with_open_attempt()
    ledger.note_outcome("op-1", WORKER, "confirmed_failed")
    ledger.note_verification("op-1", WORKER, effect="absent", result="not_verified")
    ledger.note_verification("op-1", WORKER, effect="observed", result="verified")
    assert ledger.attempt_state("op-1", "a1") == STATE_ENDED
    assert not ledger.view("op-1", 7).closed


def test_an_independent_observation_reconciles_only_an_ended_attempt() -> None:
    ledger = _ledger_with_open_attempt()
    ledger.note_verification("op-1", CHECKER, effect="absent", result="not_verified")
    assert ledger.attempt_state("op-1", "a1") == STATE_OPEN
    ledger.note_outcome("op-1", WORKER, "confirmed_done")
    ledger.note_verification("op-1", CHECKER, effect="unknown", result="not_checked")
    assert ledger.attempt_state("op-1", "a1") == STATE_ENDED
    ledger.note_verification("op-1", CHECKER, effect="absent", result="not_verified")
    assert ledger.attempt_state("op-1", "a1") == STATE_RECONCILED
    assert ledger.view("op-1", 7) == AttemptView(False, False, True, False)


def test_an_independent_verified_effect_closes_the_operation() -> None:
    ledger = _ledger_with_open_attempt()
    ledger.note_verification("op-1", CHECKER, effect="observed", result="verified")
    assert ledger.attempt_state("op-1", "a1") == STATE_CLOSED
    assert ledger.view("op-1", None).closed


def test_a_published_candidate_replaces_the_live_table() -> None:
    live = _ledger_with_open_attempt()
    candidate = copy.deepcopy(live)
    candidate.note_outcome("op-1", WORKER, "confirmed_done")
    assert live.attempt_state("op-1", "a1") == STATE_OPEN
    live.publish_from(candidate)
    assert live.attempt_state("op-1", "a1") == STATE_ENDED


def _append(store: EventStore, kind: str, payload: dict[str, Any]) -> None:
    store.append(kind, payload, durable=True)


def test_the_journal_restores_the_same_ledger_and_skips_rows_without_their_fields(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        declared = {
            "operation_id": "op-1",
            "task_id": "T1",
            "sender": WORKER,
            "recorded_at": FACT.declared_at,
            "intent": {"deadline": FACT.deadline},
            "intent_sha256": FACT.intent_sha256,
        }
        _append(store, EventKind.OPERATION_DECLARED, declared)
        _append(
            store,
            EventKind.OPERATION_DECLARED,
            {
                **declared,
                "operation_id": "op-2",
                "intent": {"deadline": True},
                "intent_sha256": None,
            },
        )
        _append(store, EventKind.OPERATION_DECLARED, {"operation_id": "op-3", "sender": WORKER})
        without_intent = {k: v for k, v in declared.items() if k != "intent"}
        _append(store, EventKind.OPERATION_DECLARED, {**without_intent, "operation_id": "op-5"})
        _append(store, EventKind.OPERATION_DECLARED, {"operation_id": 4, "sender": WORKER})
        opened = {"operation_id": "op-1", "sender": WORKER, "answer": "approved_fresh"}
        _append(store, EventKind.OPERATION_ATTEMPT_ANSWERED, {**opened, "answer": "attempt_exists"})
        _append(store, EventKind.OPERATION_ATTEMPT_ANSWERED, {**opened, "attempt_id": "a1"})
        _append(
            store,
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {**opened, "attempt_id": "a1", "approval_seq": 7},
        )
        _append(
            store,
            EventKind.OPERATION_OUTCOME,
            {"operation_id": "op-1", "sender": WORKER, "state": "confirmed_done"},
        )
        _append(
            store,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": "op-1",
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
            },
        )
        restored = AttemptLedger(store)
    finally:
        store.close()
    assert restored.operation("op-1") == FACT
    assert restored.operation("op-2") == OperationFact("T1", WORKER, None, None, FACT.declared_at)
    assert restored.operation("op-3") is None
    assert restored.attempt_state("op-1", "a1") == STATE_RECONCILED
    assert restored.view("op-1", 7) == AttemptView(False, False, True, False)
    assert restored.operation("op-5") == OperationFact(
        "T1", WORKER, FACT.intent_sha256, None, FACT.declared_at
    )
    assert sorted(restored.operations) == ["op-1", "op-2", "op-5"]


def test_attempt_states_pin_the_wire_set_and_applied_precedence_survives_restore(
    tmp_path: Path,
) -> None:
    """ATTEMPT_STATES pins the wire set; applied stays listed until observed and blocks retries."""
    assert ATTEMPT_STATES == (
        STATE_NONE,
        STATE_OPEN,
        STATE_ENDED,
        STATE_APPLIED,
        STATE_SEALED,
        STATE_RECONCILED,
        STATE_CLOSED,
    )
    attempt = "a" * 32
    digest = FACT.intent_sha256
    assert digest is not None
    marker = AttemptMarker("op-1", attempt, digest)
    objects = marker.objects("sha1")

    store = EventStore(tmp_path / "hub.db")
    try:
        _append(
            store,
            EventKind.OPERATION_DECLARED,
            {
                "operation_id": "op-1",
                "task_id": "T1",
                "sender": WORKER,
                "recorded_at": FACT.declared_at,
                "effect": "git_publication",
                "intent": {"deadline": FACT.deadline},
                "intent_sha256": digest,
            },
        )
        _append(
            store,
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {
                "operation_id": "op-1",
                "sender": WORKER,
                "answer": "approved_fresh",
                "attempt_id": attempt,
                "approval_seq": 7,
            },
        )
        stop_seq = store.append(
            EventKind.OPERATION_STOPPED,
            {
                "scope": "project:P",
                "epoch": 1,
                "listed": [{"operation_id": "op-1", "attempt_id": attempt}],
            },
            durable=True,
        )
        _append(
            store,
            EventKind.OPERATION_STOP_READBACK,
            {
                "operation_id": "op-1",
                "sender": "R/recovery",
                "stop_seq": stop_seq,
                "attempt_id": attempt,
                "result": "applied",
                "marker_ref": marker.ref,
                "object_format": "sha1",
                "marker_object": objects.applied,
            },
        )
        applied_ledger = AttemptLedger(store)
        assert applied_ledger.attempt_state("op-1", attempt) == STATE_APPLIED
        assert [item.attempt_id for item in applied_ledger.open_in("project:P")] == [attempt]
        assert [item.attempt_id for item in applied_ledger.standing_listed(stop_seq)] == [attempt]
        assert applied_ledger.actors("project:P") == frozenset({WORKER})
        assert applied_ledger.view("op-1", 8) == AttemptView(False, False, False, True, True)

        # Independent absent/not_verified reconciles the attempt and clears open_in,
        # yet view() keeps unreconciled=True and applied=True so no second attempt opens.
        _append(
            store,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": "op-1",
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
            },
        )
        moved_ledger = AttemptLedger(store)
        assert moved_ledger.attempt_state("op-1", attempt) == STATE_RECONCILED
        assert moved_ledger.open_in("project:P") == ()
        assert moved_ledger.standing_listed(stop_seq) == ()
        assert moved_ledger.view("op-1", 8) == AttemptView(False, False, False, True, True)
        assert not moved_ledger.view("op-1", 8).closed

        # Independent verified closes the operation (precedence: closed > reconciled > applied).
        _append(
            store,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": "op-1",
                "sender": CHECKER,
                "effect": "observed",
                "result": "verified",
            },
        )
        closed_ledger = AttemptLedger(store)
        assert closed_ledger.attempt_state("op-1", attempt) == STATE_CLOSED
        assert closed_ledger.view("op-1", 8).closed
    finally:
        store.close()


_ATTEMPT_1 = "1" * 32
_ATTEMPT_2 = "2" * 32


def _seed_git_operation(
    store: EventStore, *, operation_id: str = "op-perm", attempts: tuple[tuple[str, int], ...]
) -> None:
    """Append a Git declaration and one ``approved_fresh`` row per attempt."""
    _append(
        store,
        EventKind.OPERATION_DECLARED,
        {
            "operation_id": operation_id,
            "task_id": "T1",
            "sender": WORKER,
            "recorded_at": FACT.declared_at,
            "effect": "git_publication",
            "intent": {"deadline": FACT.deadline},
            "intent_sha256": FACT.intent_sha256,
        },
    )
    for attempt_id, approval_seq in attempts:
        _append(
            store,
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {
                "operation_id": operation_id,
                "task_id": "T1",
                "sender": WORKER,
                "answer": "approved_fresh",
                "attempt_id": attempt_id,
                "approval_seq": approval_seq,
            },
        )


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_marker_verification_and_end_permutations_give_identical_ledger_and_stop_state(
    tmp_path: Path, object_format: str
) -> None:
    """Every legal arrival order of end and marker verification restores the same state."""
    op = "op-perm"
    assert FACT.intent_sha256 is not None
    marker = AttemptMarker(op, _ATTEMPT_1, FACT.intent_sha256)
    sealed_oid = marker.objects(object_format).sealed
    applied_oid = marker.objects(object_format).applied

    stop_orders: tuple[tuple[str, ...], ...] = (
        ("seal", "readback", "ver"),
        ("seal", "ver", "readback"),
        ("ver", "seal", "readback"),
        ("outcome", "readback", "ver"),
        ("ver", "outcome", "readback"),
        ("ver", "readback"),
    )
    stop_results: list[tuple[str, AttemptView, str | None]] = []
    for idx, order in enumerate(stop_orders):
        store = EventStore(tmp_path / f"stop-{object_format}-{idx}.db")
        try:
            _seed_git_operation(store, operation_id=op, attempts=((_ATTEMPT_1, 7),))
            stop_seq = store.append(
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "project:P",
                    "epoch": 1,
                    "listed": [{"operation_id": op, "attempt_id": _ATTEMPT_1}],
                    "hub_time": 1_800_000_010.0,
                },
                durable=True,
            )
            for step in order:
                if step == "seal":
                    _append(
                        store,
                        EventKind.OPERATION_SEALED,
                        {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
                    )
                elif step == "outcome":
                    _append(
                        store,
                        EventKind.OPERATION_OUTCOME,
                        {"operation_id": op, "sender": WORKER, "state": "confirmed_failed"},
                    )
                elif step == "readback":
                    _append(
                        store,
                        EventKind.OPERATION_STOP_READBACK,
                        {
                            "operation_id": op,
                            "stop_seq": stop_seq,
                            "attempt_id": _ATTEMPT_1,
                            "result": "sealed",
                            "marker_ref": marker.ref,
                            "object_format": object_format,
                            "marker_object": sealed_oid,
                        },
                    )
                else:
                    _append(
                        store,
                        EventKind.OPERATION_VERIFIED,
                        {
                            "operation_id": op,
                            "sender": CHECKER,
                            "effect": "absent",
                            "result": "not_verified",
                            "evidence": {
                                "attempt_id": _ATTEMPT_1,
                                "marker_object": sealed_oid,
                            },
                        },
                    )
            reloaded = AttemptLedger(store)
            stop_state = reloaded.stop_state(stop_seq)
            stop_results.append(
                (
                    reloaded.attempt_state(op, _ATTEMPT_1),
                    reloaded.view(op, 7),
                    None if stop_state is None else stop_state.state,
                )
            )
        finally:
            store.close()

    expected_stop = (STATE_RECONCILED, AttemptView(False, False, True, False), STATE_AT_REST)
    assert stop_results == [expected_stop] * len(stop_orders)

    # Outcome + applied-marker absent verification (branch moved) is also order-independent.
    outcome_orders = (("outcome", "ver"), ("ver", "outcome"))
    outcome_results: list[tuple[str, AttemptView]] = []
    for idx, outcome_order in enumerate(outcome_orders):
        store = EventStore(tmp_path / f"outcome-{object_format}-{idx}.db")
        try:
            _seed_git_operation(store, operation_id=op, attempts=((_ATTEMPT_1, 7),))
            for step in outcome_order:
                if step == "outcome":
                    _append(
                        store,
                        EventKind.OPERATION_OUTCOME,
                        {"operation_id": op, "sender": WORKER, "state": "confirmed_done"},
                    )
                else:
                    _append(
                        store,
                        EventKind.OPERATION_VERIFIED,
                        {
                            "operation_id": op,
                            "sender": CHECKER,
                            "effect": "absent",
                            "result": "not_verified",
                            "evidence": {
                                "attempt_id": _ATTEMPT_1,
                                "marker_object": applied_oid,
                            },
                        },
                    )
            reloaded = AttemptLedger(store)
            outcome_results.append((reloaded.attempt_state(op, _ATTEMPT_1), reloaded.view(op, 7)))
        finally:
            store.close()

    expected_outcome = (STATE_RECONCILED, AttemptView(False, False, True, False))
    assert outcome_results == [expected_outcome] * len(outcome_orders)

    # Applied stop read-back + applied-marker absent verification is also order-independent.
    applied_orders = (("readback_applied", "ver"), ("ver", "readback_applied"))
    applied_results: list[tuple[str, AttemptView, str | None]] = []
    for idx, applied_order in enumerate(applied_orders):
        store = EventStore(tmp_path / f"applied-{object_format}-{idx}.db")
        try:
            _seed_git_operation(store, operation_id=op, attempts=((_ATTEMPT_1, 7),))
            stop_seq = store.append(
                EventKind.OPERATION_STOPPED,
                {
                    "scope": "project:P",
                    "epoch": 1,
                    "listed": [{"operation_id": op, "attempt_id": _ATTEMPT_1}],
                    "hub_time": 1_800_000_010.0,
                },
                durable=True,
            )
            for step in applied_order:
                if step == "readback_applied":
                    _append(
                        store,
                        EventKind.OPERATION_STOP_READBACK,
                        {
                            "operation_id": op,
                            "stop_seq": stop_seq,
                            "attempt_id": _ATTEMPT_1,
                            "result": "applied",
                            "marker_ref": marker.ref,
                            "object_format": object_format,
                            "marker_object": applied_oid,
                        },
                    )
                else:
                    _append(
                        store,
                        EventKind.OPERATION_VERIFIED,
                        {
                            "operation_id": op,
                            "sender": CHECKER,
                            "effect": "absent",
                            "result": "not_verified",
                            "evidence": {
                                "attempt_id": _ATTEMPT_1,
                                "marker_object": applied_oid,
                            },
                        },
                    )
            reloaded = AttemptLedger(store)
            stop_state = reloaded.stop_state(stop_seq)
            applied_results.append(
                (
                    reloaded.attempt_state(op, _ATTEMPT_1),
                    reloaded.view(op, 7),
                    None if stop_state is None else stop_state.state,
                )
            )
        finally:
            store.close()

    expected_applied = (
        STATE_RECONCILED,
        AttemptView(False, False, True, True, applied=True),
        STATE_AT_REST,
    )
    assert applied_results == [expected_applied] * len(applied_orders)


def test_marker_matching_isolates_attempts_and_rejects_mismatched_markers(tmp_path: Path) -> None:
    """Marker matching never leaks across attempts, rejects forged pairs, and keeps legacy rows."""
    op = "op-match"
    assert FACT.intent_sha256 is not None
    marker_1 = AttemptMarker(op, _ATTEMPT_1, FACT.intent_sha256)
    marker_2 = AttemptMarker(op, _ATTEMPT_2, FACT.intent_sha256)
    sealed_1 = marker_1.objects("sha1").sealed
    sealed_2 = marker_2.objects("sha1").sealed

    # 1. Present attempt_id naming A1 paired with A2's sealed blob must reconcile neither A1 nor A2.
    store_cross = EventStore(tmp_path / "cross-id-blob.db")
    try:
        _seed_git_operation(
            store_cross, operation_id=op, attempts=((_ATTEMPT_1, 7), (_ATTEMPT_2, 8))
        )
        _append(
            store_cross,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
        )
        _append(
            store_cross,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_2},
        )
        _append(
            store_cross,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": sealed_2},
            },
        )
        cross_ledger = AttemptLedger(store_cross)
    finally:
        store_cross.close()
    assert (
        cross_ledger.attempt_state(op, _ATTEMPT_1),
        cross_ledger.attempt_state(op, _ATTEMPT_2),
    ) == (STATE_SEALED, STATE_SEALED)

    # 2. A forged marker_object reconciles nothing (no blanket fallback) and never pre-marks open.
    store_forged = EventStore(tmp_path / "forged-blob.db")
    try:
        _seed_git_operation(store_forged, operation_id=op, attempts=((_ATTEMPT_1, 7),))
        _append(
            store_forged,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": "0" * 40},
            },
        )
        _append(
            store_forged,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
        )
        _append(
            store_forged,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": "0" * 40},
            },
        )
        forged_ledger = AttemptLedger(store_forged)
    finally:
        store_forged.close()
    assert forged_ledger.attempt_state(op, _ATTEMPT_1) == STATE_SEALED

    # 3. Pairing a sealed blob with effect="observed" is mismatched and reconciles nothing.
    store_obs_sealed = EventStore(tmp_path / "observed-sealed.db")
    try:
        _seed_git_operation(store_obs_sealed, operation_id=op, attempts=((_ATTEMPT_1, 7),))
        _append(
            store_obs_sealed,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "observed",
                "result": "not_checked",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": sealed_1},
            },
        )
        _append(
            store_obs_sealed,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
        )
        _append(
            store_obs_sealed,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "observed",
                "result": "not_checked",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": sealed_1},
            },
        )
        obs_sealed_ledger = AttemptLedger(store_obs_sealed)
    finally:
        store_obs_sealed.close()
    assert obs_sealed_ledger.attempt_state(op, _ATTEMPT_1) == STATE_SEALED

    # 4. Pre-0.99.59 journal row without attempt_id still matches A1 uniquely and never A2.
    store_legacy = EventStore(tmp_path / "legacy-no-attempt-id.db")
    try:
        _seed_git_operation(
            store_legacy, operation_id=op, attempts=((_ATTEMPT_1, 7), (_ATTEMPT_2, 8))
        )
        _append(
            store_legacy,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_2},
        )
        _append(
            store_legacy,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {"marker_object": sealed_1},
            },
        )
        _append(
            store_legacy,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
        )
        legacy_ledger = AttemptLedger(store_legacy)
    finally:
        store_legacy.close()
    assert (
        legacy_ledger.attempt_state(op, _ATTEMPT_1),
        legacy_ledger.attempt_state(op, _ATTEMPT_2),
    ) == (STATE_RECONCILED, STATE_SEALED)

    # 5. Declarer self-verification and unscoped pre-end absent verification never pre-reconcile.
    store_neg = EventStore(tmp_path / "declarer-and-unscoped.db")
    try:
        _seed_git_operation(store_neg, operation_id=op, attempts=((_ATTEMPT_1, 7),))
        _append(
            store_neg,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": WORKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {"attempt_id": _ATTEMPT_1, "marker_object": sealed_1},
            },
        )
        _append(
            store_neg,
            EventKind.OPERATION_VERIFIED,
            {
                "operation_id": op,
                "sender": CHECKER,
                "effect": "absent",
                "result": "not_verified",
                "evidence": {},
            },
        )
        _append(
            store_neg,
            EventKind.OPERATION_SEALED,
            {"operation_id": op, "sender": "R/recovery", "attempt_id": _ATTEMPT_1},
        )
        neg_ledger = AttemptLedger(store_neg)
    finally:
        store_neg.close()
    assert neg_ledger.attempt_state(op, _ATTEMPT_1) == STATE_SEALED

    # 6. Malformed marker_object, absent intent digest, non-32-hex attempt_id, unknown attempt_id,
    #    and observed+applied marker matching cover all remaining matcher edges.
    applied_1 = marker_1.objects("sha1").applied
    edge_ledger = AttemptLedger(None)
    edge_ledger.note_declaration("op-no-intent", OperationFact("T1", WORKER, None, None, 1.0))
    edge_ledger.open_attempt("op-no-intent", _ATTEMPT_1, executor=WORKER, approval_seq=1)
    edge_ledger.note_seal("op-no-intent", _ATTEMPT_1)
    edge_ledger.note_verification(
        "op-no-intent",
        CHECKER,
        effect="absent",
        result="not_verified",
        evidence={"attempt_id": _ATTEMPT_1, "marker_object": sealed_1},
    )
    assert edge_ledger.attempt_state("op-no-intent", _ATTEMPT_1) == STATE_SEALED

    edge_ledger.note_declaration(op, FACT)
    edge_ledger.open_attempt(op, "short-id", executor=WORKER, approval_seq=2)
    edge_ledger.note_seal(op, "short-id")
    edge_ledger.note_verification(
        op,
        CHECKER,
        effect="absent",
        result="not_verified",
        evidence={"attempt_id": "short-id", "marker_object": "not-a-hex-digest"},
    )
    edge_ledger.note_verification(
        op,
        CHECKER,
        effect="absent",
        result="not_verified",
        evidence={"attempt_id": "short-id", "marker_object": sealed_1},
    )
    edge_ledger.note_verification(
        op,
        CHECKER,
        effect="absent",
        result="not_verified",
        evidence={"attempt_id": "9" * 32, "marker_object": sealed_1},
    )
    assert edge_ledger.attempt_state(op, "short-id") == STATE_SEALED

    edge_ledger.open_attempt(op, _ATTEMPT_1, executor=WORKER, approval_seq=3)
    edge_ledger.note_verification(
        op,
        CHECKER,
        effect="observed",
        result="not_checked",
        evidence={"attempt_id": _ATTEMPT_1, "marker_object": applied_1},
    )
    assert edge_ledger.attempt_state(op, _ATTEMPT_1) == STATE_OPEN
    edge_ledger.note_outcome(op, WORKER, "confirmed_done")
    assert edge_ledger.attempt_state(op, _ATTEMPT_1) == STATE_RECONCILED
