# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable outbox, inbound dedupe and remote-delivery routes
"""The forwarding ledger keeps every promise made to an agent across settles and restarts."""

from __future__ import annotations

import gc
import warnings
from pathlib import Path

import pytest

from synapse_channel.core.message_forward_ledger import (
    MessageForwardLedger,
    RemoteDelivery,
    request_digest,
)
from synapse_channel.core.persistence import EventStore


def _enqueue(
    ledger: MessageForwardLedger,
    forward_id: str,
    *,
    peer_hub: str = "laptop",
    sender: str = "PROJ/alice",
    now: float = 100.0,
    ttl: float = 60.0,
    notify: bool = True,
) -> None:
    ledger.enqueue(
        forward_id=forward_id,
        peer_hub=peer_hub,
        sender=sender,
        target=f"PROJ/bob@{peer_hub}",
        request={"forward_id": forward_id, "kind": "chat"},
        now=now,
        expires_at=now + ttl,
        notify_sender=notify,
    )


def test_request_digest_is_canonical() -> None:
    """Key order does not change the digest; content does."""
    assert request_digest({"a": 1, "b": 2}) == request_digest({"b": 2, "a": 1})
    assert request_digest({"a": 1}) != request_digest({"a": 2})


def test_a_pending_entry_is_due_retried_and_settled_once() -> None:
    """Failed attempts reschedule; the first settlement wins over any later one."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "f1")
    assert [entry.forward_id for entry in ledger.due(100.0)] == ["f1"]
    assert ledger.pending_counts() == {"laptop": 1}

    failed = ledger.record_failed_attempt("f1", next_attempt_at=110.0, error="refused")
    assert failed is not None
    assert (failed.attempts, failed.next_attempt_at, failed.result) == (
        1,
        110.0,
        {"last_error": "refused"},
    )
    assert ledger.due(105.0) == []
    assert [entry.forward_id for entry in ledger.due(110.0)] == ["f1"]

    settled = ledger.settle("f1", "accepted", {"disposition": "accepted"})
    assert settled is not None
    assert (settled.state, settled.attempts) == ("accepted", 2)
    again = ledger.settle("f1", "refused", {"disposition": "refused"})
    assert again is not None
    assert (again.state, again.result) == ("accepted", {"disposition": "accepted"})
    unchanged = ledger.record_failed_attempt("f1", next_attempt_at=999.0, error="late")
    assert unchanged is not None
    assert unchanged.attempts == 2
    assert ledger.pending_counts() == {}


def test_missing_entries_answer_none() -> None:
    """Every lookup of an unknown id is ``None``, never an invented entry."""
    ledger = MessageForwardLedger.in_memory()
    assert ledger.outbox_entry("nope") is None
    assert ledger.settle("nope", "accepted", {}) is None
    assert ledger.record_failed_attempt("nope", next_attempt_at=1.0, error="x") is None
    assert ledger.inbound("laptop", "nope") is None
    assert ledger.remote_delivery("0" * 64) is None
    assert ledger.mark_sender_notified("nope", now=1.0) is False


def test_settle_refuses_a_non_settled_state() -> None:
    """``pending`` is not an outcome."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "f1")
    with pytest.raises(ValueError, match="not a settled outbox state"):
        ledger.settle("f1", "pending", {})


def test_expiry_settles_only_overdue_pending_entries_without_counting_an_attempt() -> None:
    """Overdue entries expire; settled or future ones are left alone."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "old", ttl=10.0)
    _enqueue(ledger, "answered", ttl=10.0)
    _enqueue(ledger, "fresh", ttl=1000.0)
    ledger.settle("answered", "accepted", {})
    expired = ledger.expire_due(200.0)
    assert [(entry.forward_id, entry.state, entry.attempts) for entry in expired] == [
        ("old", "expired", 0)
    ]
    assert expired[0].result == {"detail": "peer did not answer before expiry"}
    assert ledger.due(200.0)[0].forward_id == "fresh"
    assert ledger.expire_due(200.0) == []


def test_sender_notifications_are_listed_until_marked() -> None:
    """Only settled entries whose sender asked are listed, oldest first, and only once."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "later", now=200.0)
    _enqueue(ledger, "earlier", now=100.0)
    _enqueue(ledger, "silent", notify=False)
    _enqueue(ledger, "other", sender="PROJ/carol")
    _enqueue(ledger, "open")
    for forward_id in ("later", "earlier", "silent", "other"):
        ledger.settle(forward_id, "refused", {})
    assert ledger.mark_sender_notified("open", now=1.0) is False
    pending = ledger.pending_sender_notifications("PROJ/alice")
    assert [entry.forward_id for entry in pending] == ["earlier", "later"]
    assert ledger.mark_sender_notified("earlier", now=300.0) is True
    assert ledger.mark_sender_notified("earlier", now=301.0) is False
    remaining = ledger.pending_sender_notifications("PROJ/alice")
    assert [entry.forward_id for entry in remaining] == ["later"]


def test_inbound_answers_and_remote_routes_keep_the_first_record() -> None:
    """A replay never overwrites the answer or the route first stored."""
    ledger = MessageForwardLedger.in_memory()
    ledger.record_inbound("workstation", "f1", digest="d1", result={"n": 1}, now=1.0)
    ledger.record_inbound("workstation", "f1", digest="d2", result={"n": 2}, now=2.0)
    stored = ledger.inbound("workstation", "f1")
    assert stored is not None
    assert (stored.digest, stored.result) == ("d1", {"n": 1})
    assert ledger.inbound("laptop", "f1") is None

    key = "k" * 64
    first = RemoteDelivery(operation_key=key, peer_hub="laptop", sender="A/a", target="A/b@laptop")
    ledger.remember_remote_delivery(first, now=1.0)
    ledger.remember_remote_delivery(
        RemoteDelivery(operation_key=key, peer_hub="evil", sender="A/x", target="A/b@evil"),
        now=2.0,
    )
    assert ledger.remote_delivery(key) == first


def test_state_survives_a_restart_of_the_event_store(tmp_path: Path) -> None:
    """A durable hub reopens with its pending outbox, answers and routes intact."""
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        _enqueue(store.message_forward, "f1")
        store.message_forward.record_inbound("peer", "f9", digest="d", result={"ok": 1}, now=1.0)
        store.message_forward.remember_remote_delivery(
            RemoteDelivery(operation_key="k" * 64, peer_hub="laptop", sender="A/a", target="t"),
            now=1.0,
        )
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        ledger = reopened.message_forward
        entry = ledger.outbox_entry("f1")
        assert entry is not None
        assert (entry.state, entry.notify_sender, entry.request["kind"]) == (
            "pending",
            True,
            "chat",
        )
        assert ledger.inbound("peer", "f9") is not None
        assert ledger.remote_delivery("k" * 64) is not None
    finally:
        reopened.close()


def test_a_corrupt_row_is_refused_rather_than_trusted() -> None:
    """An unknown state or a non-object JSON column fails loudly on read."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "bad-state")
    _enqueue(ledger, "bad-json")
    connection = ledger._conn
    connection.execute(
        "UPDATE message_forward_outbox SET state = 'lost' WHERE forward_id = 'bad-state'"
    )
    connection.execute(
        "UPDATE message_forward_outbox SET result_json = '[1]' WHERE forward_id = 'bad-json'"
    )
    connection.commit()
    with pytest.raises(ValueError, match="state 'lost' is unknown"):
        ledger.outbox_entry("bad-state")
    with pytest.raises(ValueError, match="not a JSON object"):
        ledger.outbox_entry("bad-json")


def test_pending_summary_reports_depth_and_oldest_age_per_peer() -> None:
    """Only unanswered forwards count; ages are measured from local acceptance."""
    ledger = MessageForwardLedger.in_memory()
    _enqueue(ledger, "a1", peer_hub="laptop", now=100.0)
    _enqueue(ledger, "a2", peer_hub="laptop", now=130.0)
    _enqueue(ledger, "b1", peer_hub="server", now=150.0)
    _enqueue(ledger, "done", peer_hub="server", now=90.0)
    ledger.settle("done", "accepted", {})
    summary = ledger.pending_summary(160.0)
    assert {
        peer: (item.pending, item.oldest_pending_seconds) for peer, item in summary.items()
    } == {
        "laptop": (2, 60.0),
        "server": (1, 10.0),
    }
    assert ledger.pending_summary(0.0)["laptop"].oldest_pending_seconds == 0.0
    assert MessageForwardLedger.in_memory().pending_summary(1.0) == {}

    gc.collect()
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", ResourceWarning)
        for _ in range(10):
            retired = MessageForwardLedger.in_memory()
            _enqueue(retired, "retired")
            assert retired.pending_counts() == {"laptop": 1}
            del retired
        gc.collect()
    assert not [warning for warning in observed if issubclass(warning.category, ResourceWarning)]
