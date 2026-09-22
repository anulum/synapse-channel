# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — attention projection through durable source records
"""Exercise attention evidence from real hub and private entitlement events."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.attention import project_hub_attention, project_quota_attention
from synapse_channel.core.attention_store import (
    AttentionStoreError,
    default_attention_store,
    mark_notified,
    queue_view,
    set_alert_state,
    sync_evidence,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_pending_approval_expiry_never_becomes_approval(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(subject="task-1", state="requested"),
            },
            durable=True,
        )
        events = tuple(store.iter_events(kinds={EventKind.LEDGER_PROGRESS}))
        overdue = project_hub_attention(events, now=events[0].ts + 61, approval_ttl_seconds=60)
        assert len(overdue) == 1
        assert overdue[0].state == "expired"
        assert overdue[0].severity == "critical"
        assert overdue[0].subject == "task-1"
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:two",
                "text": format_approval_note(subject="task-1", state="approved"),
            },
            durable=True,
        )
        decided = project_hub_attention(
            store.iter_events(kinds={EventKind.LEDGER_PROGRESS}), now=events[0].ts + 61
        )
        assert len(decided) == 1
        assert decided[0].state == "resolved"
        assert decided[0].source_revision != overdue[0].source_revision
    finally:
        store.close()


def test_failed_delivery_recovery_uses_exact_message_sequence(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            {"message_seq": 19, "delivered": False, "reason": "offline"},
            durable=True,
        )
        failed = project_hub_attention(store.iter_events(), now=0)
        assert len(failed) == 1
        assert failed[0].key == "delivery:19"
        assert failed[0].state == "open"
        store.append(
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            {"message_seq": 19, "delivered": True, "deferred": True},
            durable=True,
        )
        recovered = project_hub_attention(store.iter_events(), now=0)
        assert len(recovered) == 1
        assert recovered[0].state == "resolved"
        assert recovered[0].kind == "recovery"
        assert recovered[0].source_revision != failed[0].source_revision
        store.append(
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            {"message_seq": 20, "delivered": True},
            durable=True,
        )
        current = project_hub_attention(store.iter_events(), now=0)
        assert {item.key: item.state for item in current} == {
            "delivery:19": "resolved",
            "delivery:20": "resolved",
        }
    finally:
        store.close()


def test_stale_quota_and_reset_omit_private_account_label(tmp_path: Path) -> None:
    common = {
        "recorded_at": "2026-09-19T00:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
    }
    events: list[dict[str, object]] = [
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "account-1",
            "label": "Secret subscription name",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "pool-1",
            "account_id": "account-1",
            "unit": "tokens",
        },
        {
            **common,
            "event_id": "w1",
            "kind": "window",
            "window_id": "window-1",
            "pool_id": "pool-1",
            "starts_at": "2026-09-19T00:00:00Z",
            "ends_at": "2026-09-21T00:00:00Z",
            "renewal_at": "2026-09-21T00:00:00Z",
            "grant": "100",
            "unit": "tokens",
            "price_revision": "price-1",
        },
        {
            **common,
            "event_id": "b1",
            "kind": "balance",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-balance-1",
            "remaining": "0",
            "observed_at": "2026-09-19T00:00:00Z",
        },
    ]
    at = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    report = project_quota_attention(
        events,
        at=at,
        stale_after_seconds=3600,
    )
    assert {item.kind for item in report} == {"stale_data", "quota_reset"}
    assert all("Secret" not in str(item) for item in report)
    no_observation = project_quota_attention(events[:-1], at=at, stale_after_seconds=3600)
    assert len(no_observation) == 1 and no_observation[0].kind == "stale_data"
    assert no_observation[0].source_revision.endswith("unobserved")
    events.append(
        {
            **common,
            "event_id": "b2",
            "kind": "balance",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-balance-2",
            "remaining": "50",
            "observed_at": "2026-09-20T11:59:00Z",
        }
    )
    fresh = project_quota_attention(events, at=at, stale_after_seconds=3600)
    assert fresh == ()
    queue = tmp_path / "private" / "queue.db"
    assert sync_evidence(queue, source="quota", evidence=report, now=100)["created"] == 2
    assert sync_evidence(queue, source="quota", evidence=fresh, now=101)["changed"] == 2
    assert queue_view(queue, now=101)["state"] == "quiet"
    after_window = project_quota_attention(
        events, at=datetime(2026, 9, 22, tzinfo=timezone.utc), stale_after_seconds=3600
    )
    assert after_window == ()


def test_irrelevant_or_malformed_hub_records_cannot_become_alerts(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        store.append(EventKind.LEDGER_PROGRESS, {"kind": "note", "text": "approval requested"})
        store.append(
            EventKind.LEDGER_PROGRESS, {"kind": "approval", "text": "not a canonical note"}
        )
        store.append(
            EventKind.DELIVERY_RECEIPT_IMMEDIATE, {"message_seq": False, "delivered": False}
        )
        store.append(EventKind.DELIVERY_RECEIPT_IMMEDIATE, {"message_seq": 3})
        store.append(EventKind.DEAD_LETTER_ESCALATION, {"target": ""})
        assert project_hub_attention(store.iter_events(), now=100) == ()
        store.append(EventKind.DEAD_LETTER_ESCALATION, {"target": "peer-A", "count": 3})
        alert = project_hub_attention(store.iter_events(), now=100)[0]
        assert alert.key == "dead-letter:peer-A"
        assert alert.action == "Inspect the dead-letter queue and recover the target"
    finally:
        store.close()


def test_attention_refuses_invalid_time_bounds() -> None:
    with pytest.raises(ValueError, match="review deadline"):
        project_hub_attention((), now=1, approval_ttl_seconds=0)
    with pytest.raises(ValueError, match="stale age"):
        project_quota_attention((), at=datetime.now(timezone.utc), stale_after_seconds=0)


def test_queue_deduplicates_snoozes_and_reopens_on_new_hub_evidence(tmp_path: Path) -> None:
    hub = EventStore(tmp_path / "hub.db")
    queue = tmp_path / "private" / "attention.db"
    try:
        hub.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(subject="task-1", state="requested"),
            },
            durable=True,
        )
        evidence = project_hub_attention(hub.iter_events(), now=100)
        with pytest.raises(AttentionStoreError, match="duplicate"):
            sync_evidence(queue, source="hub", evidence=(*evidence, *evidence), now=100)
        with pytest.raises(AttentionStoreError, match="evidence is invalid"):
            sync_evidence(queue, source="hub", evidence=(replace(evidence[0], key=""),), now=100)
        assert sync_evidence(queue, source="hub", evidence=evidence, now=100)["created"] == 1
        assert sync_evidence(queue, source="hub", evidence=evidence, now=101)["unchanged"] == 1
        assert queue_view(queue, now=101)["state"] == "active"
        assert queue_view(queue, now=500)["state"] == "missing_observer"
        set_alert_state(queue, "approval:task-1", action="snooze", now=102, until=200)
        assert queue_view(queue, now=103)["alerts"] == []
        assert len(queue_view(queue, now=201)["alerts"]) == 1
        set_alert_state(queue, "approval:task-1", action="resolve", now=202)
        assert queue_view(queue, now=203)["alerts"] == []
        with pytest.raises(AttentionStoreError, match="already resolved"):
            set_alert_state(queue, "approval:task-1", action="resolve", now=203)
        assert sync_evidence(queue, source="hub", evidence=evidence, now=204)["unchanged"] == 1
        hub.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(subject="task-1", state="requested"),
            },
            durable=True,
        )
        refreshed = project_hub_attention(hub.iter_events(), now=205)
        assert sync_evidence(queue, source="hub", evidence=refreshed, now=205)["changed"] == 1
        assert len(queue_view(queue, now=205)["alerts"]) == 1
    finally:
        hub.close()


def test_delivery_recovery_is_one_alert_until_resolution(tmp_path: Path) -> None:
    hub = EventStore(tmp_path / "hub.db")
    queue = tmp_path / "private" / "attention.db"
    try:
        hub.append(
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            {"message_seq": 21, "delivered": False},
            durable=True,
        )
        sync_evidence(
            queue, source="hub", evidence=project_hub_attention(hub.iter_events(), now=1), now=1
        )
        assert mark_notified(queue, ["delivery:21"], now=2) == 1
        assert mark_notified(queue, ["delivery:21"], now=3) == 0
        hub.append(
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            {"message_seq": 21, "delivered": True},
            durable=True,
        )
        recovered = project_hub_attention(hub.iter_events(), now=4)
        assert sync_evidence(queue, source="hub", evidence=recovered, now=4)["changed"] == 1
        row = queue_view(queue, now=4)["alerts"][0]
        assert row["kind"] == "recovery"
        assert mark_notified(queue, ["delivery:21"], now=5) == 1
        assert sync_evidence(queue, source="hub", evidence=recovered, now=6)["unchanged"] == 1
        assert queue_view(queue, now=6)["alerts"][0]["kind"] == "recovery"
        set_alert_state(queue, "delivery:21", action="resolve", now=7)
        assert queue_view(queue, now=8)["state"] == "quiet"
        assert sync_evidence(queue, source="hub", evidence=recovered, now=9)["unchanged"] == 1
    finally:
        hub.close()


def test_resolved_delivery_without_prior_failure_stays_out_of_queue(tmp_path: Path) -> None:
    hub = EventStore(tmp_path / "hub.db")
    try:
        hub.append(EventKind.DELIVERY_RECEIPT_IMMEDIATE, {"message_seq": 5, "delivered": True})
        evidence = project_hub_attention(hub.iter_events(), now=100)
        queue = tmp_path / "private" / "attention.db"
        assert sync_evidence(queue, source="hub", evidence=evidence, now=100) == {
            "created": 0,
            "changed": 0,
            "unchanged": 0,
        }
        assert queue_view(queue, now=100)["state"] == "quiet"
    finally:
        hub.close()


def test_attention_store_path_and_version_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_attention_store() == tmp_path / "synapse-channel/attention/queue.sqlite3"
    monkeypatch.setenv("XDG_STATE_HOME", "relative-path")
    assert default_attention_store().is_absolute()
    queue = tmp_path / "private" / "queue.db"
    queue_view(queue, now=100)
    with sqlite3.connect(queue) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(AttentionStoreError, match="unsupported attention store version"):
        queue_view(queue, now=100)


def test_private_queue_refuses_unowned_or_invalid_storage_and_transitions(tmp_path: Path) -> None:
    queue = tmp_path / "private" / "queue.db"
    assert queue_view(queue, now=100)["state"] == "missing_observer"
    with pytest.raises(AttentionStoreError, match="alert not found"):
        set_alert_state(queue, "approval:absent", action="resolve", now=100)
    with pytest.raises(AttentionStoreError, match="snooze"):
        set_alert_state(queue, "approval:absent", action="snooze", now=100, until=99)
    with pytest.raises(AttentionStoreError, match="invalid attention action"):
        set_alert_state(queue, "approval:absent", action="dismiss", now=100)
    with pytest.raises(AttentionStoreError, match="observer age"):
        queue_view(queue, now=100, observer_age_limit=float("nan"))
    with pytest.raises(AttentionStoreError, match="notification batch"):
        mark_notified(queue, ["duplicate", "duplicate"], now=100)
    with pytest.raises(AttentionStoreError, match="source"):
        sync_evidence(queue, source="unknown", evidence=(), now=100)
    with pytest.raises(AttentionStoreError, match="observer time"):
        sync_evidence(queue, source="hub", evidence=(), now=float("nan"))
    symlink = tmp_path / "link.db"
    symlink.symlink_to(queue)
    with pytest.raises(AttentionStoreError, match="symlink"):
        queue_view(symlink, now=100)
