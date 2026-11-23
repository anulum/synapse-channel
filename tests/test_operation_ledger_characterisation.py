# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — one journal the attempt ledger restores whole
"""Pin the ledger a handler journal restores, before its owners are split."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.attempt_markers import AttemptMarker
from synapse_channel.core.handlers.operation_triggers import note_written, write_trigger
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger, AttemptSummary, AttemptView
from synapse_channel.core.operation_attempts import APPROVED_FRESH, OperationFact
from synapse_channel.core.operation_level_criteria import (
    ITEM_INCORRECT_ACCEPTANCES,
    CriteriaItem,
    criteria_digest,
)
from synapse_channel.core.operation_level_ledger import LevelCriteria, LevelJudgement
from synapse_channel.core.operation_stop_ledger import StopLedger
from synapse_channel.core.operation_stop_readbacks import (
    RESULT_APPLIED,
    RESULT_SEALED,
    RESULT_UNREAD,
)
from synapse_channel.core.operation_stop_states import StopState
from synapse_channel.core.persistence import EventStore

WORKER = "P/worker"
CHECKER = "Q/checker"
RECOVERY = "R/recovery"
JUDGE = "S/judge"
STOPPER = "S/stopper"
DIGEST = "d" * 64
SEAL_ATTEMPT = "a" * 32
APPLIED_ATTEMPT = "b" * 32
LATE_ATTEMPT = "c" * 32
HELD_ATTEMPT = "e" * 32
DECLARED_AT = 1_800_000_000.0
STOP_TIME = 1_800_000_010.0
DEADLINE = 1_800_000_050
NOW = 1_800_000_100.0
STOP_BOUND = 30.0
SCOPE = "project:P"
HELD = "team:held"


def _declare(operation_id: str, *, deadline: int, effect: str) -> dict[str, Any]:
    """Return the declaration row the record handler persists."""
    return {
        "operation_id": operation_id,
        "task_id": "T1",
        "sender": WORKER,
        "recorded_at": DECLARED_AT,
        "effect": effect,
        "intent": {"deadline": deadline, "required_assurance": "cooperative"},
        "intent_sha256": DIGEST,
    }


def _open(operation_id: str, attempt_id: str, approval_seq: int) -> dict[str, Any]:
    """Return the approved-fresh row the attempt handler persists."""
    return {
        "operation_id": operation_id,
        "sender": WORKER,
        "answer": APPROVED_FRESH,
        "attempt_id": attempt_id,
        "approval_seq": approval_seq,
    }


def _picture(ledger: AttemptLedger, *, criteria_seq: int) -> dict[str, Any]:
    """Return the public reads the split must keep."""
    operations = ("op-seal", "op-applied", "op-late", "op-held")
    stops = ledger.stops_listing("op-seal") + ledger.stops_listing("op-held")
    since = ledger.window_start(SCOPE, criteria_seq)
    triggers = ledger.due_triggers(NOW, STOP_BOUND, 0.0)
    return {
        "summaries": {name: ledger.summary(name) for name in operations},
        "views": {name: ledger.view(name, 7) for name in operations},
        "states": {
            "op-seal": ledger.attempt_state("op-seal", SEAL_ATTEMPT),
            "op-applied": ledger.attempt_state("op-applied", APPLIED_ATTEMPT),
            "op-late": ledger.attempt_state("op-late", LATE_ATTEMPT),
            "op-held": ledger.attempt_state("op-held", HELD_ATTEMPT),
        },
        "contradicted": {
            "op-seal": ledger.contradicted_at("op-seal"),
            "op-applied": ledger.contradicted_at("op-applied"),
        },
        "stop_states": {seq: ledger.stop_state(seq) for seq in stops},
        "stop_facts": {name: ledger.stop_facts(name) for name in operations},
        "level_facts": {name: ledger.level_facts(name) for name in operations},
        "level_counts": ledger.level_counts(SCOPE, since, NOW),
        "level_actors": ledger.level_actors(SCOPE, since),
        "due": [(item.cause, item.scope, dict(item.subject)) for item in triggers],
        "criteria": sorted(ledger.criteria),
        "judgements": sorted(ledger.judgements),
    }


def _journal(store: EventStore) -> tuple[AttemptLedger, int]:
    """Journal one handler sequence and note the same calls on a live ledger.

    The rows are the ones the record, stop, level and trigger handlers persist.
    """
    live = AttemptLedger(None)

    def add(kind: str, payload: dict[str, Any]) -> int:
        return store.append(kind, payload, durable=True)

    raised = add(EventKind.OPERATION_LEVEL, {"scope": SCOPE, "level": "A2", "epoch": 1})
    live.note_level(SCOPE, raised, level="A2", epoch=1)
    step_down = add(EventKind.OPERATION_LEVEL, {"scope": SCOPE, "level": "A1", "epoch": 2})
    live.note_level(SCOPE, step_down, level="A1", epoch=2)
    items = (CriteriaItem(ITEM_INCORRECT_ACCEPTANCES, 1, True),)
    digest = criteria_digest(SCOPE, "A2", items)
    criteria_seq = add(
        EventKind.LEVEL_CRITERIA,
        {
            "scope": SCOPE,
            "target": "A2",
            "items": [item.to_json() for item in items],
            "digest": digest,
            "declared_by": JUDGE,
        },
    )
    live.note_criteria(digest, LevelCriteria(criteria_seq, SCOPE, "A2", items, JUDGE))

    def declared(operation_id: str, *, deadline: int, effect: str) -> None:
        add(EventKind.OPERATION_DECLARED, _declare(operation_id, deadline=deadline, effect=effect))
        live.note_declaration(
            operation_id,
            OperationFact("T1", WORKER, DIGEST, deadline, DECLARED_AT, effect, "cooperative"),
        )

    def opened(operation_id: str, attempt_id: str, approval_seq: int) -> None:
        add(EventKind.OPERATION_ATTEMPT_ANSWERED, _open(operation_id, attempt_id, approval_seq))
        live.open_attempt(operation_id, attempt_id, executor=WORKER, approval_seq=approval_seq)

    def outcome(operation_id: str, state: str) -> None:
        seq = add(
            EventKind.OPERATION_OUTCOME,
            {"operation_id": operation_id, "sender": WORKER, "state": state},
        )
        live.note_outcome(operation_id, WORKER, state, seq=seq)

    declared("op-seal", deadline=1_900_000_000, effect="git_publication")
    opened("op-seal", SEAL_ATTEMPT, 7)
    outcome("op-seal", "confirmed_done")
    declared("op-applied", deadline=1_900_000_000, effect="git_publication")
    opened("op-applied", APPLIED_ATTEMPT, 8)
    outcome("op-applied", "not_started")
    stop_seq = add(
        EventKind.OPERATION_STOPPED,
        {
            "scope": SCOPE,
            "reason": "seal",
            "hub_time": STOP_TIME,
            "request_key": "stop-p",
            "stopper": STOPPER,
            "epoch": 1,
            "listed": [
                {"operation_id": "op-seal", "attempt_id": SEAL_ATTEMPT},
                {"operation_id": "op-applied", "attempt_id": APPLIED_ATTEMPT},
            ],
        },
    )
    live.note_stop(
        SCOPE,
        stop_seq,
        epoch=1,
        listed=(("op-seal", SEAL_ATTEMPT), ("op-applied", APPLIED_ATTEMPT)),
        recorded_at=STOP_TIME,
    )
    unread = add(
        EventKind.OPERATION_STOP_READBACK,
        {
            "operation_id": "op-seal",
            "sender": RECOVERY,
            "stop_seq": stop_seq,
            "attempt_id": SEAL_ATTEMPT,
            "result": RESULT_UNREAD,
        },
    )
    live.note_readback(stop_seq, SEAL_ATTEMPT, RESULT_UNREAD, seq=unread, operation_id="op-seal")
    sealed_marker = AttemptMarker("op-seal", SEAL_ATTEMPT, DIGEST)
    sealed_seq = add(
        EventKind.OPERATION_STOP_READBACK,
        {
            "operation_id": "op-seal",
            "sender": RECOVERY,
            "stop_seq": stop_seq,
            "attempt_id": SEAL_ATTEMPT,
            "result": RESULT_SEALED,
            "marker_ref": sealed_marker.ref,
            "object_format": "sha1",
            "marker_object": sealed_marker.objects("sha1").sealed,
        },
    )
    live.note_readback(
        stop_seq,
        SEAL_ATTEMPT,
        RESULT_SEALED,
        seq=sealed_seq,
        operation_id="op-seal",
        marker_ref=sealed_marker.ref,
        object_format="sha1",
        marker_object=sealed_marker.objects("sha1").sealed,
    )
    applied_marker = AttemptMarker("op-applied", APPLIED_ATTEMPT, DIGEST)
    applied_seq = add(
        EventKind.OPERATION_STOP_READBACK,
        {
            "operation_id": "op-applied",
            "sender": RECOVERY,
            "stop_seq": stop_seq,
            "attempt_id": APPLIED_ATTEMPT,
            "result": RESULT_APPLIED,
            "marker_ref": applied_marker.ref,
            "object_format": "sha1",
            "marker_object": applied_marker.objects("sha1").applied,
        },
    )
    live.note_readback(
        stop_seq,
        APPLIED_ATTEMPT,
        RESULT_APPLIED,
        seq=applied_seq,
        operation_id="op-applied",
        marker_ref=applied_marker.ref,
        object_format="sha1",
        marker_object=applied_marker.objects("sha1").applied,
    )
    resume_seq = add(
        EventKind.OPERATION_RESUMED,
        {
            "scope": SCOPE,
            "reason": "clear",
            "hub_time": NOW,
            "request_key": "resume-p",
            "resumer": STOPPER,
            "lifts_seq": stop_seq,
            "case": "operator",
        },
    )
    live.note_resume(SCOPE, resume_seq)
    declared("op-late", deadline=DEADLINE, effect="file")
    opened("op-late", LATE_ATTEMPT, 9)
    outcome("op-late", "not_started")
    declared("op-held", deadline=1_900_000_000, effect="file")
    opened("op-held", HELD_ATTEMPT, 10)
    held_seq = add(
        EventKind.OPERATION_STOPPED,
        {
            "scope": HELD,
            "reason": "hold",
            "hub_time": STOP_TIME,
            "request_key": "stop-held",
            "stopper": STOPPER,
            "epoch": 1,
            "listed": [{"operation_id": "op-held", "attempt_id": HELD_ATTEMPT}],
        },
    )
    live.note_stop(
        HELD,
        held_seq,
        epoch=1,
        listed=(("op-held", HELD_ATTEMPT),),
        recorded_at=STOP_TIME,
    )
    judgement_seq = add(
        EventKind.LEVEL_JUDGEMENT,
        {"digest": digest, "verdict": "met", "judge": JUDGE},
    )
    live.note_judgement(judgement_seq, LevelJudgement(digest, "met", JUDGE))
    return live, criteria_seq


def _triggers(store: EventStore, live: AttemptLedger) -> None:
    """Journal the two causes the watcher would write, and note them live."""
    due = live.due_triggers(NOW, STOP_BOUND, 0.0)
    for trigger in due:
        sequences = write_trigger(store, trigger, NOW)
        note_written(live, trigger, sequences)


def test_a_handler_journal_restores_the_same_ledger_as_the_live_notes(tmp_path: Path) -> None:
    """Replay and the live notes agree, including both trigger causes."""
    store = EventStore(tmp_path / "hub.db")
    try:
        live, criteria_seq = _journal(store)
        replayed = AttemptLedger(store)
        assert _picture(replayed, criteria_seq=criteria_seq) == _picture(
            live, criteria_seq=criteria_seq
        )
        before = _picture(live, criteria_seq=criteria_seq)
        assert [item[0] for item in before["due"]] == ["deadline_unverified", "stop_unsettled"]
        _triggers(store, live)
        restored = AttemptLedger(store)
        assert _picture(restored, criteria_seq=criteria_seq) == _picture(
            live, criteria_seq=criteria_seq
        )
        assert restored.due_triggers(NOW, STOP_BOUND, 0.0) == []
        assert restored.contradicted_at("op-seal") > 0
        assert restored.contradicted_at("op-applied") > 0
        assert (
            restored.readback_of(restored.stops_listing("op-seal")[0], SEAL_ATTEMPT)
            == RESULT_SEALED
        )
    finally:
        store.close()


def test_publication_installs_every_table_by_reference(tmp_path: Path) -> None:
    """The hub's actor publishes each table as the candidate's own object."""
    store = EventStore(tmp_path / "hub.db")
    try:
        live, _criteria_seq = _journal(store)
        _triggers(store, live)
        candidate = AttemptLedger(store)
        published = AttemptLedger(None)
        published.publish_from(candidate)
        assert published.operations is candidate.operations
        assert published.stops is candidate.stops
        assert published.stop_rows is candidate.stop_rows
        assert published.levels is candidate.levels
        assert published.criteria is candidate.criteria
        assert published.judgements is candidate.judgements
    finally:
        store.close()


_SEAL = "a" * 32
_APPLIED = "b" * 32
_LATE = "c" * 32
_HELD = "e" * 32
_PIN: dict[str, Any] = {
    "summaries": {
        "op-seal": (False, (AttemptSummary(_SEAL, WORKER, "sealed"),)),
        "op-applied": (False, (AttemptSummary(_APPLIED, WORKER, "applied"),)),
        "op-late": (False, (AttemptSummary(_LATE, WORKER, "ended"),)),
        "op-held": (False, (AttemptSummary(_HELD, WORKER, "open"),)),
    },
    "views": {
        "op-seal": AttemptView(False, False, True, True, False),
        "op-applied": AttemptView(False, False, False, True, True),
        "op-late": AttemptView(False, False, False, True, False),
        "op-held": AttemptView(False, True, False, False, False),
    },
    "states": {
        "op-seal": "sealed",
        "op-applied": "applied",
        "op-late": "ended",
        "op-held": "open",
    },
    "contradicted": {"op-seal": 12, "op-applied": 13},
    "stop_states": {
        10: StopState(
            "no new effect",
            "cooperative",
            (
                ("op-seal", _SEAL, "awaiting an independent observation"),
                ("op-applied", _APPLIED, "awaiting an independent observation"),
            ),
            {
                "listed": 2,
                "sealed": 1,
                "applied": 1,
                "unsealable": 0,
                "unread": 0,
                "reconciled": 0,
            },
        ),
        20: StopState(
            "stop requested",
            "cooperative",
            (("op-held", _HELD, "unknown: no marker read back"),),
            {
                "listed": 1,
                "sealed": 0,
                "applied": 0,
                "unsealable": 0,
                "unread": 0,
                "reconciled": 0,
            },
        ),
    },
    "stop_facts": {
        "op-seal": (None, ("project:P", 14)),
        "op-applied": (None, ("project:P", 14)),
        "op-late": (None, ("project:P", 14)),
        "op-held": (None, ("project:P", 14)),
    },
    "level_facts": {
        "op-seal": ("A1", ("project:P", 2)),
        "op-applied": ("A1", ("project:P", 2)),
        "op-late": ("A1", ("project:P", 2)),
        "op-held": ("A1", ("project:P", 2)),
    },
    "level_counts": {"incorrect_acceptances": 2, "unknown_outcomes": 1, "stops_at_rest": 0},
    "level_actors": frozenset({WORKER}),
    "due": [],
    "criteria": ["fa95155e4d61ef517cafd147c87fdd939810e407517ef99721a1a4a730507a90"],
    "judgements": [21],
}


def test_the_restored_reads_stay_pinned(tmp_path: Path) -> None:
    """The public reads stay at the values first measured on this base."""
    store = EventStore(tmp_path / "hub.db")
    try:
        _live, criteria_seq = _journal(store)
        _triggers(store, _live)
        picture = _picture(AttemptLedger(store), criteria_seq=criteria_seq)
    finally:
        store.close()
    assert picture == _PIN


def test_a_bool_stop_sequence_does_not_mark_sequence_one_bound_triggered() -> None:
    """``True`` compares equal to 1 and must not mark the stop recorded at sequence 1."""
    ledger = StopLedger()
    ledger.note_stop(SCOPE, 1, epoch=1, listed=())
    ledger.mark_bound_triggered(True)
    assert ledger.stop_rows[1].bound_triggered is False
