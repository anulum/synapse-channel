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
        db.execute("PRAGMA user_version=3")
    with pytest.raises(AppTaskError, match="unsupported"):
        get(store, "safe", now=at)


def test_mcp_offer_binds_its_identity_through_result_attachment(tmp_path: Path) -> None:
    """A second MCP identity cannot take over a private task's result channel."""
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    bundle = _bundle("bound", at)
    assert offer(store, bundle, ledger=ledger, actor="TEST/first", now=at)["offered_by"] == (
        "TEST/first"
    )
    assert offer(store, bundle, ledger=ledger, actor="TEST/first", now=at)["state"] == "offered"
    with pytest.raises(AppTaskError, match="another offerer"):
        offer(store, bundle, ledger=ledger, actor="TEST/second", now=at)
    advance(store, "bound", "accept", now=at)
    advance(store, "bound", "start", now=at)
    with pytest.raises(AppTaskError, match="only the task offerer"):
        attach(store, "bound", _result("bound"), actor="TEST/second", require_offerer=True, now=at)
    assert get(store, "bound", now=at)["state"] == "running"
    assert (
        attach(store, "bound", _result("bound"), actor="TEST/first", require_offerer=True, now=at)[
            "state"
        ]
        == "result_attached"
    )


def test_v1_task_store_migrates_with_legacy_operator_custody(tmp_path: Path) -> None:
    """Existing local tasks remain available without becoming MCP-owned."""
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    offer(store, _bundle("legacy", at), ledger=ledger, now=at)
    with sqlite3.connect(store) as db:
        db.execute("ALTER TABLE tasks DROP COLUMN offered_by")
        db.execute("PRAGMA user_version=1")
    assert get(store, "legacy", now=at)["offered_by"] == "operator:legacy"
    with sqlite3.connect(store) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_future_dated_allowance_is_not_offered(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at + timedelta(hours=2))
    with pytest.raises(AppTaskError, match="dated in the future"):
        offer(tmp_path / "queue" / "queue.sqlite3", _bundle("future", at), ledger=ledger, now=at)


def test_offer_refuses_missing_allowance_and_expired_bundle(tmp_path: Path) -> None:
    at = _at()
    store = tmp_path / "queue" / "queue.sqlite3"
    with pytest.raises(AppTaskError, match="allowance window not found"):
        offer(store, _bundle("missing-ledger", at), ledger=tmp_path / "absent.db", now=at)
    with pytest.raises(AppTaskError, match="already expired"):
        offer(store, _bundle("expired", at, seconds=-1), now=at)
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_offer_refuses_corrupt_allowance_ledger_without_accepting_work(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "corrupt.db"
    ledger.write_bytes(b"not a SQLite entitlement ledger")
    store = tmp_path / "queue.db"
    with pytest.raises(AppTaskError, match="allowance ledger unavailable"):
        offer(store, _bundle("corrupt-ledger", at), ledger=ledger, now=at)
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_app_task_rejects_unbounded_offer_and_result_identities(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    with pytest.raises(AppTaskError, match="offer actor must be a bounded identity"):
        offer(store, _bundle("bound", at), ledger=ledger, actor=" ", now=at)
    offer(store, _bundle("bound", at), ledger=ledger, actor="operator:owner", now=at)
    advance(store, "bound", "accept", now=at)
    advance(store, "bound", "start", now=at)
    with pytest.raises(AppTaskError, match="result actor must be a bounded identity"):
        attach(store, "bound", _result("bound"), actor="x" * 129, now=at)
    assert get(store, "bound", now=at)["state"] == "running"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"task_id": "bad space"}, "bounded opaque id"),
        ({"prompt": "   "}, "prompt must be nonempty"),
        ({"input": []}, "input must be a JSON object"),
        ({"input": {"score": float("nan")}}, "finite JSON"),
        ({"input": {"payload": "x" * 1_048_576}}, "exceeds one MiB"),
        ({"verifier": {"field": "bad space", "equals": True}}, "verifier requires"),
        ({"verifier": {"field": "ok", "equals": []}}, "verifier requires"),
    ],
)
def test_offer_rejects_invalid_or_oversized_work_before_store_creation(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    at = _at()
    store = tmp_path / "queue" / "queue.sqlite3"
    with pytest.raises(AppTaskError, match=message):
        offer(store, {**_bundle("bad", at), **change}, now=at)
    assert not store.exists()


def test_offer_refuses_incomplete_bundle_before_any_allowance_lookup(tmp_path: Path) -> None:
    at = _at()
    store = tmp_path / "queue.sqlite3"
    bundle = _bundle("incomplete", at)
    del bundle["verifier"]
    with pytest.raises(AppTaskError, match="bundle requires"):
        offer(store, bundle, ledger=tmp_path / "absent.db", now=at)
    assert not store.exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"payload": []}, "result requires"),
        ({"provenance": " "}, "result requires"),
        ({"usage": {"amount": "1", "measurement": "estimated"}}, "usage requires"),
        ({"usage": {"amount": "1"}}, "usage requires"),
    ],
)
def test_invalid_result_cannot_replace_running_task(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    offer(store, _bundle("bound", at), ledger=ledger, now=at)
    advance(store, "bound", "accept", now=at)
    advance(store, "bound", "start", now=at)
    with pytest.raises(AppTaskError, match=message):
        attach(store, "bound", {**_result("bound"), **change}, now=at)
    assert get(store, "bound", now=at)["state"] == "running"


def test_unknown_transition_missing_task_and_unverified_correction_refuse(tmp_path: Path) -> None:
    at = _at()
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, at)
    store = tmp_path / "queue" / "queue.sqlite3"
    offer(store, _bundle("bound", at), ledger=ledger, now=at)
    with pytest.raises(AppTaskError, match="unknown task transition"):
        advance(store, "bound", "erase", now=at)
    with pytest.raises(AppTaskError, match="task not found"):
        get(store, "absent", now=at)
    with pytest.raises(AppTaskError, match="verified task"):
        correct_usage(store, "bound", "1", "legitimate reason", now=at)
    with pytest.raises(AppTaskError, match="bounded reason"):
        correct_usage(store, "bound", "1", " ", now=at)
    assert get(store, "bound", now=at)["state"] == "offered"
