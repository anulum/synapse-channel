# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable delivery admission and outbox tests
"""Check atomic request admission, deduplication and restart persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

import pytest

from synapse_channel.core.delivery_modes import (
    DeliveryIntent,
    DeliveryRefusal,
    DeliveryStage,
    parse_delivery_intent,
)
from synapse_channel.core.delivery_persistence import (
    DELIVERY_ACCEPTED,
    DELIVERY_QUEUED,
    DeliveryWrite,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _intent(*, sender: str = "P/author", **changes: Any) -> DeliveryIntent:
    """Create a valid parsed intent while varying its durable identity or body."""
    frame: dict[str, Any] = {
        "sender": "spoofed",
        "type": "delivery_request",
        "target": "P/receiver",
        "protocol_version": 3,
        "request_id": "req-1",
        "idempotency_key": "idem-1",
        "target_incarnation": "a" * 64,
        "mode": "follow_up",
        "allowed_fallbacks": ["next_turn"],
        "task_id": "T-1",
        "body": "Review this change.",
        "deadline": 160.0,
    }
    frame.update(changes)
    return parse_delivery_intent(frame, sender=sender, origin_hub="hub-1", now=100.0)


def _offer(intent: DeliveryIntent, **changes: Any) -> dict[str, Any]:
    """Build the stable recipient frame committed with the queued request."""
    frame: dict[str, Any] = {
        "type": "delivery_offer",
        "operation_key": intent.operation_key,
        "notification_id": f"delivery:{intent.operation_key}:1",
        "target": intent.target,
        "target_incarnation": intent.target_incarnation,
        "request_id": intent.request_id,
        "mode": "follow_up",
        "body": intent.body,
    }
    frame.update(changes)
    return frame


def _create(store: EventStore, intent: DeliveryIntent, **offer_changes: Any) -> DeliveryWrite:
    """Queue one intent through the event store's public delivery ledger."""
    return store.delivery.create(
        intent,
        selected_mode="follow_up",
        quality="native",
        offer=_offer(intent, **offer_changes),
    )


def test_admission_is_atomic_and_replays_after_restart(tmp_path: Path) -> None:
    """An accepted request, queue event, aggregate and offer survive one reopen."""
    path = tmp_path / "hub.db"
    intent = _intent()
    with EventStore(path) as store:
        result = _create(store, intent)
        assert result.disposition == "inserted"
        assert result.record.stage == "queued"
        assert result.record.ordinal == 1
        assert result.record.quality == "native"
        assert result.record.latest_event_seq > 0
        assert store.delivery.pending_for(intent.target, intent.target_incarnation) == (
            result.record,
        )
        assert store.delivery.notification(f"delivery:{intent.operation_key}:1") == _offer(intent)
        assert [event.kind for event in store.iter_events()] == [
            EventKind.DELIVERY_INTENT_ACCEPTED,
            EventKind.DELIVERY_INTENT_QUEUED,
        ]
        assert EventKind.DELIVERY_INTENT_ACCEPTED == DELIVERY_ACCEPTED
        assert EventKind.DELIVERY_INTENT_QUEUED == DELIVERY_QUEUED

    with EventStore(path) as restored:
        assert restored.delivery.get(intent.operation_key) == result.record
        assert restored.delivery.pending_for(intent.target, intent.target_incarnation) == (
            result.record,
        )
        assert _create(restored, intent).disposition == "replayed"
        assert len(tuple(restored.iter_events())) == 2


def test_delivery_pages_refuse_unbounded_reads(tmp_path: Path) -> None:
    """Every public replay reader enforces the same bounded page contract."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        _create(store, intent)
        readers = (
            lambda limit: store.delivery.pending_for(
                intent.target, intent.target_incarnation, limit=limit
            ),
            lambda limit: store.delivery.due_for_expiry(200.0, limit=limit),
            lambda limit: store.delivery.open_for_other_incarnations(
                intent.target, "b" * 64, limit=limit
            ),
            lambda limit: store.delivery.pending_notifications(intent.target, limit=limit),
        )
        for reader in readers:
            assert reader(1)
            for limit in (0, 129):
                with pytest.raises(ValueError, match="page limit"):
                    reader(limit)


@pytest.mark.parametrize(
    ("statement", "value"),
    [
        ("UPDATE delivery_requests SET request_json = ? WHERE operation_key = ?", '{"profile":2}'),
        ("UPDATE delivery_requests SET quality = ? WHERE operation_key = ?", "unknown"),
        ("UPDATE delivery_requests SET stage = ? WHERE operation_key = ?", "unknown"),
    ],
)
def test_live_delivery_read_refuses_corrupt_aggregate(
    tmp_path: Path, statement: str, value: str
) -> None:
    """A damaged indexed row is never projected as a successful live status."""
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        intent = _intent()
        _create(store, intent)
        with sqlite3.connect(path) as damaged:
            damaged.execute(statement, (value, intent.operation_key))
        with pytest.raises(DeliveryRefusal) as caught:
            store.delivery.get(intent.operation_key)
        assert caught.value.code == "replay_incompatible"


def test_reopen_migrates_legacy_notification_retirement_column(tmp_path: Path) -> None:
    """A durable pre-retirement outbox gains the new column on real reopen."""
    path = tmp_path / "hub.db"
    with sqlite3.connect(path) as legacy:
        legacy.execute(
            "CREATE TABLE delivery_notifications ("
            "notification_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL, "
            "audience TEXT NOT NULL, frame_json TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, delivered_at REAL)"
        )
    with EventStore(path):
        with sqlite3.connect(path) as migrated:
            columns = {
                row[1] for row in migrated.execute("PRAGMA table_info(delivery_notifications)")
            }
    assert "retired_at" in columns


def test_hub_identity_check_refuses_malformed_durable_request(tmp_path: Path) -> None:
    """A corrupt persisted request fails the public hub identity check."""
    path = tmp_path / "hub.db"
    intent = _intent()
    with EventStore(path) as store:
        _create(store, intent)
        with sqlite3.connect(path) as damaged:
            damaged.execute(
                "UPDATE delivery_requests SET request_json = ? WHERE operation_key = ?",
                ("{", intent.operation_key),
            )
        with pytest.raises(DeliveryRefusal) as caught:
            store.delivery.verify_origin_hub("hub-1")
        assert caught.value.code == "replay_incompatible"


def test_quarantine_write_rolls_back_on_storage_refusal(tmp_path: Path) -> None:
    """A SQLite refusal leaves no partial quarantine row or open transaction."""
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        with sqlite3.connect(path) as damaged:
            damaged.execute(
                "CREATE TRIGGER refuse_quarantine BEFORE INSERT ON delivery_quarantine "
                "BEGIN SELECT RAISE(ABORT, 'storage refusal'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="storage refusal"):
            store.delivery.quarantine("hub-1:operation", "replay_incompatible")
        with sqlite3.connect(path) as checked:
            assert checked.execute("SELECT count(*) FROM delivery_quarantine").fetchone() == (0,)
            checked.execute("DROP TRIGGER refuse_quarantine")
        store.delivery.quarantine("hub-1:operation", "replay_incompatible")
        with sqlite3.connect(path) as checked:
            assert checked.execute("SELECT count(*) FROM delivery_quarantine").fetchone() == (1,)


def test_request_id_and_idempotency_key_conflicts_leave_original_intact(tmp_path: Path) -> None:
    """Same id with changed body or same idempotency key with a new id is refused."""
    with EventStore(tmp_path / "hub.db") as store:
        original = _intent()
        accepted = _create(store, original)
        changed_body = _create(store, _intent(body="Change permissions."))
        new_request_id = _create(store, _intent(request_id="req-2"))
        assert changed_body.disposition == "conflict"
        assert new_request_id.disposition == "conflict"
        assert changed_body.record == accepted.record
        assert new_request_id.record == accepted.record
        assert len(tuple(store.iter_events())) == 2


def test_notification_failure_rolls_back_events_and_aggregate(tmp_path: Path) -> None:
    """A failure after event inserts cannot leave an orphaned positive queue state."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        with pytest.raises(ValueError, match="Out of range float"):
            _create(store, intent, unexpected=float("nan"))
        assert store.delivery.get(intent.operation_key) is None
        assert tuple(store.iter_events()) == ()
        assert _create(store, intent).disposition == "inserted"


@pytest.mark.parametrize(
    "change",
    [
        {"notification_id": "wrong"},
        {"operation_key": "wrong"},
        {"target": "P/other"},
        {"target_incarnation": "b" * 64},
    ],
)
def test_offer_must_bind_exact_request(tmp_path: Path, change: dict[str, Any]) -> None:
    """A mismatched recipient offer is refused before journal mutation."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        with pytest.raises(DeliveryRefusal) as failure:
            _create(store, intent, **change)
        assert failure.value.code == "invalid_shape"
        assert tuple(store.iter_events()) == ()


def test_selected_capability_must_be_sender_allowed(tmp_path: Path) -> None:
    """The ledger cannot silently choose a mode outside the sender's list."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        with pytest.raises(DeliveryRefusal) as failure:
            store.delivery.create(
                intent, selected_mode="interrupt", quality="native", offer=_offer(intent)
            )
        assert failure.value.code == "invalid_shape"
        assert tuple(store.iter_events()) == ()


def test_absent_notification_is_unknown(tmp_path: Path) -> None:
    """Querying an unknown outbox identity cannot imply delivery."""
    with EventStore(tmp_path / "hub.db") as store:
        assert store.delivery.notification("missing") is None


def test_restart_between_boundary_and_ack_preserves_correlation(tmp_path: Path) -> None:
    """A recipient may resume the same request after a committed boundary stage."""
    path = tmp_path / "hub.db"
    intent = _intent()
    with EventStore(path) as store:
        _create(store, intent)
        boundary = store.delivery.advance(
            intent.operation_key,
            stage="boundary_delivered",
            mutation_id="boundary-1",
            mutation_digest="b" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"boundary": "turn-1"},
        )
        assert boundary.record.stage == "boundary_delivered"
        assert boundary.record.ordinal == 2
        notice = store.delivery.notification(f"delivery:{intent.operation_key}:2")
        assert notice is not None
        assert notice["stage"] == "boundary_delivered"
        assert notice["source"] == "recipient"
    with EventStore(path) as restored:
        assert restored.delivery.get(intent.operation_key) == boundary.record
        ack = restored.delivery.advance(
            intent.operation_key,
            stage="acknowledged",
            mutation_id="ack-1",
            mutation_digest="c" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"receipt_id": "local-1"},
        )
        assert ack.record.stage == "acknowledged"
        assert ack.record.ordinal == 3
        assert restored.delivery.pending_for(intent.target, intent.target_incarnation) == ()


def test_cancel_completion_race_retains_both_facts(tmp_path: Path) -> None:
    """A cancellation request cannot erase a recipient's completed outcome."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        _create(store, intent)
        stages: tuple[DeliveryStage, ...] = ("boundary_delivered", "acknowledged")
        for ordinal, stage in enumerate(stages, start=2):
            store.delivery.advance(
                intent.operation_key,
                stage=stage,
                mutation_id=f"recipient-{ordinal}",
                mutation_digest=f"{ordinal}" * 64,
                actor="P/receiver",
                source="recipient",
                evidence={},
            )
        requested = store.delivery.request_cancel(
            intent.operation_key,
            mutation_id="cancel-1",
            mutation_digest="a" * 64,
            actor="P/author",
        )
        assert requested.record.stage == "acknowledged"
        assert requested.record.cancel_requested
        cancel_notice = store.delivery.notification(f"delivery:{intent.operation_key}:4")
        assert cancel_notice is not None
        assert cancel_notice["cancel_requested"]
        completed = store.delivery.advance(
            intent.operation_key,
            stage="completed",
            mutation_id="outcome-1",
            mutation_digest="b" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"request_id": "req-1", "task_id": "T-1", "executor_ref": "run-1"},
        )
        assert completed.record.stage == "completed"
        assert completed.record.cancel_requested
        replayed = store.delivery.advance(
            intent.operation_key,
            stage="completed",
            mutation_id="outcome-1",
            mutation_digest="b" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"request_id": "req-1", "task_id": "T-1", "executor_ref": "run-1"},
        )
        assert replayed.disposition == "replayed"
        assert replayed.record == completed.record
        conflict = store.delivery.advance(
            intent.operation_key,
            stage="completed",
            mutation_id="outcome-1",
            mutation_digest="c" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"request_id": "req-1", "task_id": "T-1", "executor_ref": "run-2"},
        )
        assert conflict.disposition == "conflict"
        assert len(tuple(store.iter_events())) == 6


@pytest.mark.parametrize(
    ("actor", "source", "stage", "evidence", "code"),
    [
        ("P/attacker", "recipient", "boundary_delivered", {}, "unauthorised_requester"),
        ("hub-1", "hub", "boundary_delivered", {}, "unauthorised_requester"),
        ("P/receiver", "recipient", "expired", {}, "unauthorised_requester"),
        ("wrong-hub", "hub", "expired", {}, "unauthorised_requester"),
        ("P/receiver", "recipient", "completed", {}, "invalid_shape"),
    ],
)
def test_spoofed_or_uncorrelated_transition_is_refused(
    tmp_path: Path,
    actor: str,
    source: Literal["recipient", "hub"],
    stage: DeliveryStage,
    evidence: dict[str, Any],
    code: str,
) -> None:
    """Recipient outcomes need an exact actor and task/request correlation."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        accepted = _create(store, intent)
        with pytest.raises(DeliveryRefusal) as failure:
            store.delivery.advance(
                intent.operation_key,
                stage=stage,
                mutation_id="mutation-1",
                mutation_digest="b" * 64,
                actor=actor,
                source=source,
                evidence=evidence,
            )
        assert failure.value.code == code
        assert store.delivery.get(intent.operation_key) == accepted.record
        assert len(tuple(store.iter_events())) == 2


def test_hub_expiry_and_sender_cancel_authority(tmp_path: Path) -> None:
    """Only the owning hub may expire a queue and only its sender may cancel."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        _create(store, intent)
        with pytest.raises(DeliveryRefusal) as failure:
            store.delivery.request_cancel(
                intent.operation_key,
                mutation_id="cancel-1",
                mutation_digest="a" * 64,
                actor="P/attacker",
            )
        assert failure.value.code == "unauthorised_requester"
        expiry = store.delivery.advance(
            intent.operation_key,
            stage="expired",
            mutation_id="deadline-160",
            mutation_digest="e" * 64,
            actor="hub-1",
            source="hub",
            evidence={"reason_code": "deadline_expired"},
        )
        assert expiry.record.stage == "expired"
        assert store.delivery.pending_for(intent.target, intent.target_incarnation) == ()
        with pytest.raises(DeliveryRefusal) as terminal:
            store.delivery.request_cancel(
                intent.operation_key,
                mutation_id="cancel-2",
                mutation_digest="f" * 64,
                actor="P/author",
            )
        assert terminal.value.code == "invalid_transition"


def test_recipient_queue_limit_refuses_without_partial_admission(tmp_path: Path) -> None:
    """An unbounded sender cannot grow one live recipient incarnation indefinitely."""
    with EventStore(tmp_path / "hub.db") as store:
        for index in range(128):
            intent = _intent(
                sender=f"P/author-{index // 16}",
                request_id=f"req-{index}",
                idempotency_key=f"idem-{index}",
            )
            assert _create(store, intent).disposition == "inserted"
        overflow = _intent(
            sender="P/author-overflow", request_id="req-overflow", idempotency_key="idem-overflow"
        )
        with pytest.raises(DeliveryRefusal) as failure:
            _create(store, overflow)
        assert failure.value.code == "recipient_queue_full"
        assert store.delivery.get(overflow.operation_key) is None
        assert len(tuple(store.iter_events())) == 256
        first = _intent(sender="P/author-0", request_id="req-0", idempotency_key="idem-0")
        store.delivery.advance(
            first.operation_key,
            stage="expired",
            mutation_id="queue-slot-expired",
            mutation_digest="e" * 64,
            actor="hub-1",
            source="hub",
            evidence={"reason_code": "deadline_elapsed"},
        )
        assert _create(store, overflow).disposition == "inserted"


def test_sender_queue_share_refuses_without_using_every_recipient_slot(tmp_path: Path) -> None:
    """One sender cannot occupy all 128 slots of a recipient incarnation."""
    with EventStore(tmp_path / "hub.db") as store:
        for index in range(16):
            intent = _intent(request_id=f"req-{index}", idempotency_key=f"idem-{index}")
            assert _create(store, intent).disposition == "inserted"
        overflow = _intent(request_id="req-17", idempotency_key="idem-17")
        with pytest.raises(DeliveryRefusal) as failure:
            _create(store, overflow)
        assert failure.value.code == "recipient_queue_full"
        assert store.delivery.get(overflow.operation_key) is None
        assert (
            _create(store, _intent(sender="P/other", request_id="other")).disposition == "inserted"
        )


def test_repeated_cancellation_does_not_append_or_notify(tmp_path: Path) -> None:
    """Changing mutation ids cannot amplify a single cancellation into 100 writes."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        _create(store, intent)
        first = store.delivery.request_cancel(
            intent.operation_key,
            mutation_id="cancel-0",
            mutation_digest="a" * 64,
            actor="P/author",
        )
        events = len(tuple(store.iter_events()))
        pending = store.delivery.pending_notifications(intent.target)
        for index in range(1, 101):
            repeated = store.delivery.request_cancel(
                intent.operation_key,
                mutation_id=f"cancel-{index}",
                mutation_digest="b" * 64,
                actor="P/author",
            )
            assert repeated.disposition == "replayed"
            assert repeated.record == first.record
        assert len(tuple(store.iter_events())) == events
        assert store.delivery.pending_notifications(intent.target) == pending


def test_terminal_transition_retires_stale_recipient_offer(tmp_path: Path) -> None:
    """A replaced recipient never replays an offer from a terminal intent."""
    with EventStore(tmp_path / "hub.db") as store:
        intent = _intent()
        _create(store, intent)
        assert len(store.delivery.pending_notifications(intent.target)) == 1
        store.delivery.advance(
            intent.operation_key,
            stage="superseded",
            mutation_id="superseded-1",
            mutation_digest="c" * 64,
            actor="hub-1",
            source="hub",
            evidence={"reason_code": "recipient_session_replaced"},
        )
        assert store.delivery.pending_notifications(intent.target) == ()
        assert store.delivery.notification(f"delivery:{intent.operation_key}:1") is not None
