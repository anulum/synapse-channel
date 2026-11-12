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

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    STATE_CLOSED,
    STATE_ENDED,
    STATE_NONE,
    STATE_OPEN,
    STATE_RECONCILED,
    AttemptLedger,
    AttemptView,
)
from synapse_channel.core.operation_attempts import OperationFact
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
