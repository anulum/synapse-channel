# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private human app task lifecycle tests
"""Exercise durable offer, return, refusal, expiry and usage corrections."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from synapse_channel.core.app_tasks import (
    AppTaskError,
    advance,
    attach,
    correct_usage,
    get,
    history,
    offer,
    verify,
)
from synapse_channel.core.entitlement_store import append_event


def _at() -> datetime:
    return datetime(2026, 9, 22, 22, 0, tzinfo=timezone.utc)


def _ledger(path: Path, at: datetime) -> None:
    common = {
        "recorded_at": (at - timedelta(hours=1)).isoformat(),
        "source": "operator:owner",
        "confidence": "operator",
    }
    for event in (
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "a1",
            "label": "Private",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "p1",
            "account_id": "a1",
            "unit": "tasks",
        },
        {
            **common,
            "event_id": "w1",
            "kind": "window",
            "window_id": "w1",
            "pool_id": "p1",
            "starts_at": (at - timedelta(days=1)).isoformat(),
            "ends_at": (at + timedelta(days=1)).isoformat(),
            "grant": "5",
            "unit": "tasks",
            "price_revision": "unknown",
        },
    ):
        assert append_event(path, event)


def _bundle(task_id: str, at: datetime, *, seconds: int = 600) -> dict[str, object]:
    return {
        "task_id": task_id,
        "prompt": "Ask the app to produce a reviewed answer.",
        "input": {"question": "Which source?"},
        "window_id": "w1",
        "expires_at": (at + timedelta(seconds=seconds)).isoformat(),
        "verifier": {"field": "reviewed", "equals": True},
    }


def _result(task_id: str, reviewed: bool = True) -> dict[str, object]:
    return {
        "task_id": task_id,
        "payload": {"reviewed": reviewed, "answer": "Untrusted result text"},
        "provenance": "operator:manual-app-upload",
        "usage": {"amount": "1", "measurement": "manual"},
    }


def test_full_lifecycle_and_duplicate_wrong_task_controls(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    first = offer(store, _bundle("task-a", at), ledger=ledger, now=at)
    assert first["state"] == "offered"
    assert first["allowance"]["source"] == "operator:owner"
    assert first["allowance"]["source_age_seconds"] == 3600
    assert offer(store, _bundle("task-a", at), ledger=ledger, now=at)["state"] == "offered"
    with pytest.raises(AppTaskError, match="different bundle"):
        offer(store, {**_bundle("task-a", at), "prompt": "changed"}, ledger=ledger, now=at)
    assert advance(store, "task-a", "accept", now=at)["state"] == "accepted"
    assert advance(store, "task-a", "start", now=at)["state"] == "running"
    with pytest.raises(AppTaskError, match="exactly its task"):
        attach(store, "task-a", _result("task-b"), now=at)
    bad = _result("task-a", reviewed=False)
    assert attach(store, "task-a", bad, now=at)["state"] == "result_attached"
    assert attach(store, "task-a", bad, now=at)["state"] == "result_attached"
    with pytest.raises(AppTaskError, match="cannot attach"):
        attach(store, "task-a", _result("task-a"), now=at)
    with pytest.raises(AppTaskError, match="verifier did not pass"):
        verify(store, "task-a", now=at)
    assert get(store, "task-a", now=at)["state"] == "result_attached"
    assert advance(store, "task-a", "cancel", now=at)["state"] == "cancelled"
    with pytest.raises(AppTaskError, match="cannot verify"):
        verify(store, "task-a", now=at)
    assert os.stat(store).st_mode & 0o777 == 0o600
    assert os.stat(store.parent).st_mode & 0o777 == 0o700


def test_verified_result_manual_correction_and_expiry(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    offer(store, _bundle("done", at), ledger=ledger, now=at)
    advance(store, "done", "accept", now=at)
    advance(store, "done", "start", now=at)
    attach(store, "done", _result("done"), now=at)
    assert verify(store, "done", now=at)["state"] == "verified"
    amended = correct_usage(store, "done", "2.5", "receipt correction", now=at)
    assert amended["usage"]["amount"] == "2.5"
    assert amended["usage"]["measurement"] == "manual"
    assert amended["result"]["usage"]["amount"] == "1"
    attach_event = next(event for event in history(store, "done") if event["action"] == "attach")
    assert attach_event["detail"]["actor"] == "operator:cli"
    assert len(attach_event["detail"]["sha256"]) == 64
    with sqlite3.connect(store) as db:
        assert [r[0] for r in db.execute("SELECT action FROM events WHERE task_id='done'")] == [
            "offer",
            "accept",
            "start",
            "attach",
            "verify",
            "correct_usage",
        ]
    offer(store, _bundle("late", at, seconds=1), ledger=ledger, now=at)
    assert get(store, "late", now=at + timedelta(seconds=2))["state"] == "expired"
    assert (
        offer(store, _bundle("late", at, seconds=1), ledger=ledger, now=at + timedelta(days=2))[
            "state"
        ]
        == "expired"
    )
    with pytest.raises(AppTaskError, match="cannot accept"):
        advance(store, "late", "accept", now=at + timedelta(seconds=2))
    offer(store, _bundle("declined", at), ledger=ledger, now=at)
    assert advance(store, "declined", "decline", now=at)["state"] == "declined"


def test_store_refuses_symlink_and_wrong_version(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    offer(store, _bundle("safe", at), ledger=ledger, now=at)
    alias = store.parent / "alias.sqlite3"
    alias.symlink_to(store)
    with pytest.raises(AppTaskError, match="symlink"):
        get(alias, "safe", now=at)
    with sqlite3.connect(store) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(AppTaskError, match="unsupported"):
        get(store, "safe", now=at)


def test_future_dated_allowance_is_not_offered(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at + timedelta(hours=2))
    with pytest.raises(AppTaskError, match="dated in the future"):
        offer(tmp_path / "queue" / "queue.sqlite3", _bundle("future", at), ledger=ledger, now=at)
