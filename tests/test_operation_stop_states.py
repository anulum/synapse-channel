# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the state of a stop computed from its records, with its profile
"""A stop's state reads only kept read-backs and reconciliation, and its profile is honest."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.operation_attempts import OperationFact
from synapse_channel.core.operation_stop_readbacks import CLOSING_RESULTS
from synapse_channel.core.operation_stop_states import (
    PROFILE_COOPERATIVE,
    PROFILE_ISOLATED,
    REASON_AWAITING,
    REASON_UNKNOWN,
    STATE_AT_REST,
    STATE_NO_NEW_EFFECT,
    STATE_STOP_REQUESTED,
    STOP_STATES,
    ListedAttempt,
    compute_stop_state,
    profile_for,
)
from synapse_channel.core.persistence import EventStore

WORKER = "P/worker"
CHECKER = "Q/checker"


def _state(*items: ListedAttempt) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    computed = compute_stop_state(items, closing=CLOSING_RESULTS, profile=PROFILE_COOPERATIVE)
    return computed.state, computed.blocking


def test_the_states_advance_by_read_backs_then_reconciliation() -> None:
    assert STOP_STATES == ("stop requested", "no new effect", "at rest")
    assert _state() == (STATE_AT_REST, ())
    unread = ListedAttempt("op", "a1", "unread", reconciled=True)
    unsealable = ListedAttempt("op", "a2", "unsealable", reconciled=False)
    silent = ListedAttempt("op", "a3", None, reconciled=False)
    sealed = ListedAttempt("op", "a4", "sealed", reconciled=False)
    applied = ListedAttempt("op", "a5", "applied", reconciled=True)
    assert _state(unread, unsealable, silent, sealed) == (
        STATE_STOP_REQUESTED,
        (
            ("op", "a1", "unread"),
            ("op", "a2", "unsealable"),
            ("op", "a3", REASON_UNKNOWN),
            ("op", "a4", REASON_AWAITING),
        ),
    )
    assert _state(sealed, applied) == (STATE_NO_NEW_EFFECT, (("op", "a4", REASON_AWAITING),))
    assert _state(applied) == (STATE_AT_REST, ())
    computed = compute_stop_state(
        (unsealable, sealed, applied), closing=CLOSING_RESULTS, profile=PROFILE_COOPERATIVE
    )
    assert computed.counts == {
        "listed": 3,
        "sealed": 1,
        "applied": 1,
        "unsealable": 1,
        "unread": 0,
        "reconciled": 1,
    }
    assert computed.to_payload()["profile_says"] == (
        "governed effects only: a writer that does not ask the hub is not stopped by this stop"
    )
    assert computed.rank() == 0


def test_isolated_is_said_only_with_its_evidence_and_every_intent_requiring_it() -> None:
    assert profile_for(["isolated", "isolated"], isolation_evidence=False) == PROFILE_COOPERATIVE
    assert profile_for(["isolated", "controlled"], isolation_evidence=True) == PROFILE_COOPERATIVE
    assert profile_for([], isolation_evidence=True) == PROFILE_COOPERATIVE
    assert profile_for(["isolated"], isolation_evidence=True) == PROFILE_ISOLATED


def _fact(declarer: str, assurance: str | None = "isolated") -> OperationFact:
    return OperationFact("T1", declarer, "d" * 64, None, 1.0, "git_publication", assurance)


def test_the_ledger_never_reads_a_report_and_counts_only_independent_observations() -> None:
    ledger = AttemptLedger(None)
    ledger.note_declaration("op", _fact(WORKER))
    ledger.open_attempt("op", "a1", executor=WORKER, approval_seq=3)
    ledger.note_stop("project:P", 10, epoch=1, listed=(("op", "a1"),))
    assert ledger.stop_state(99) is None
    ledger.note_outcome("op", WORKER, "not_started")
    stated = ledger.stop_state(10)
    assert stated is not None and stated.state == STATE_STOP_REQUESTED
    assert stated.profile == PROFILE_COOPERATIVE
    ledger.note_readback(10, "a1", "sealed")
    ledger.note_verification("op", WORKER, effect="absent", result="not_verified")
    assert (ledger.stop_state(10) or stated).state == STATE_NO_NEW_EFFECT
    ledger.note_verification("op", CHECKER, effect="absent", result="not_verified")
    assert (ledger.stop_state(10) or stated).state == STATE_AT_REST
    assert ledger.stops_listing("op") == (10,) and ledger.stops_listing("other") == ()
    assert ledger.stop_identity(10) == ("project:P", 1) and ledger.stop_identity(11) is None


def test_a_verified_operation_counts_as_reconciled_and_a_restart_computes_the_same(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        declared = {"task_id": "T1", "sender": WORKER, "recorded_at": 1.0, "operation_id": "op"}
        store.append(
            EventKind.OPERATION_DECLARED,
            {
                **declared,
                "effect": "git_publication",
                "intent_sha256": "d" * 64,
                "intent": {"required_assurance": "isolated"},
            },
        )
        store.append(
            EventKind.OPERATION_ATTEMPT_ANSWERED,
            {
                "operation_id": "op",
                "sender": WORKER,
                "answer": "approved_fresh",
                "attempt_id": "a1",
                "approval_seq": 1,
            },
        )
        listed = [{"operation_id": "op", "attempt_id": "a1"}]
        stopped = store.append(
            EventKind.OPERATION_STOPPED, {"scope": "project:P", "epoch": 1, "listed": listed}
        )
        store.append(
            EventKind.OPERATION_STOP_READBACK,
            {
                "operation_id": "op",
                "sender": "R/r",
                "stop_seq": stopped,
                "attempt_id": "a1",
                "result": "applied",
            },
        )
        store.append(
            EventKind.OPERATION_VERIFIED,
            {"operation_id": "op", "sender": CHECKER, "effect": "observed", "result": "verified"},
        )
        restored = AttemptLedger(store)
    finally:
        store.close()
    fact = restored.operation("op")
    assert fact is not None and fact.assurance == "isolated"
    computed = restored.stop_state(stopped)
    assert computed is not None
    assert (computed.state, computed.profile) == (STATE_AT_REST, PROFILE_COOPERATIVE)


def test_an_unknown_listed_operation_is_not_reconciled_and_a_bare_resume_freezes_nothing() -> None:
    ledger = AttemptLedger(None)
    ledger.note_stop("installation", 4, epoch=1, listed=(("ghost", "a9"),))
    ledger.note_readback(4, "a9", "applied")
    stated = ledger.stop_state(4)
    assert stated is not None and stated.state == STATE_NO_NEW_EFFECT
    ledger.note_resume("project:none", 5)
    assert ledger.standing_stop("project:none") is None
    assert (ledger.stop_state(4) or stated).state == STATE_NO_NEW_EFFECT
