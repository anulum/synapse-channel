# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator-fenced notification recovery
"""Recover failed hints through real private policy and durable SQLite files."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_http_helpers import provision
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.wake_admission import WakeAdmission
from synapse_channel.mcp.wake_control import WakeControl
from synapse_channel.mcp.wake_database import MAX_WAKE_RECEIPTS
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeAttempt, WakeLedgerError


def operator_ledger(directory: Path) -> tuple[Path, WakeLedger, WakeAttempt]:
    """Create a genuine provisioned profile with one durable failed notification."""
    path = directory / "grants.json"
    provision(path, Ed25519PrivateKey.generate())
    policy = json.loads(path.read_text())
    grant = policy["subjects"]["alice"]["projects"]["ALPHA"]
    grant["tools"].extend(["synapse_inbox_page", "synapse_owned_work"])
    grant["wake"] = {
        "url": "https://bot.example.test/wake",
        "signing_key_file": str(directory / "key"),
    }
    path.write_text(json.dumps(policy))
    admission = WakeAdmission(path, "alice", "ALPHA", load_http_auth_config(path))
    ledger = WakeLedger(directory / ".synapse-mcp-wake", admission.scope("operator-hub"))
    ledger.observe_page(0, 1, sources=("messages",))
    attempt = ledger.acquire(100)
    assert attempt is not None
    assert ledger.finish(attempt, 101, "rejected", http_status=401)
    return path, ledger, attempt


def test_redrive_preserves_body_and_rejects_an_old_same_second_completion(tmp_path: Path) -> None:
    path, ledger, old = operator_ledger(tmp_path)
    control = WakeControl(path, "alice", "ALPHA")
    assert control.status()["seat"] == "ALPHA/alice"
    assert control.act("redrive", old.delivery_id, old.attempt, 100)["outcome"] == "queued"
    assert control.act("redrive", old.delivery_id, old.attempt, 100)["outcome"] == "already_applied"
    current = ledger.acquire(100)
    assert current is not None
    assert current.delivery_id == old.delivery_id and current.body == old.body
    assert current.deadline == old.deadline and current.attempt == old.attempt + 1
    assert not ledger.finish(old, 100, "accepted", http_status=202)
    assert ledger.finish(current, 100, "retry", http_status=503)
    assert ledger.summary()["next_attempt_at"] == 101
    assert ledger.acquire(100) is None
    retry = ledger.acquire(101)
    assert retry is not None and retry.attempt == current.attempt + 1
    assert ledger.finish(retry, 102, "accepted", http_status=202)
    status = control.status()
    assert status["summary"]["transport_accepted"] == 1
    assert status["summary"]["unresolved_failures"] == 0
    assert status["deliveries"][0]["control_action"] == "redrive"
    assert ledger.scan_cursor() == 1


def test_each_redrive_has_four_attempts_but_lifetime_fence_never_repeats(tmp_path: Path) -> None:
    _, ledger, old = operator_ledger(tmp_path)
    assert ledger.control_delivery("redrive", old.delivery_id, old.attempt, 102) == "queued"
    now = 102
    current = ledger.acquire(now)
    assert current is not None
    for delay in (1, 5, 30):
        assert ledger.finish(current, now, "retry", http_status=503)
        now += delay
        current = ledger.acquire(now)
        assert current is not None
    assert current.attempt == 5
    assert ledger.finish(current, now, "retry", http_status=503)
    assert ledger.summary()["unresolved_failures"] == 1
    assert ledger.control_delivery("retire", old.delivery_id, old.attempt, now) == "changed"
    assert ledger.control_delivery("redrive", old.delivery_id, 5, now) == "queued"
    renewed = ledger.acquire(now)
    assert renewed is not None and renewed.attempt == 6
    assert not ledger.finish(current, now, "accepted", http_status=202)


def test_active_work_blocks_an_unrelated_redrive_and_exact_retirement_is_repeatable(
    tmp_path: Path,
) -> None:
    path, ledger, failed = operator_ledger(tmp_path)
    ledger.observe_page(1, 2, sources=("tasks",))
    active = ledger.acquire(102)
    assert active is not None
    control = WakeControl(path, "alice", "ALPHA")
    assert control.act("redrive", failed.delivery_id, failed.attempt, 102)["outcome"] == "busy"
    assert control.act("retire", failed.delivery_id, failed.attempt, 102)["outcome"] == "retired"
    assert (
        control.act("retire", failed.delivery_id, failed.attempt, 103)["outcome"]
        == "already_applied"
    )
    assert ledger.control_delivery("redrive", active.delivery_id, active.attempt, 103) == "changed"
    assert ledger.control_delivery("redrive", "msg_" + "0" * 32, 1, 103) == "unknown"
    assert ledger.finish(active, 103, "accepted", http_status=202)
    assert ledger.scan_cursor() == 2


def test_unresolved_failures_fill_the_budget_without_silently_dropping_work(tmp_path: Path) -> None:
    _, ledger, first = operator_ledger(tmp_path)
    for cursor in range(2, MAX_WAKE_RECEIPTS + 1):
        ledger.observe_page(cursor - 1, cursor, sources=("messages",))
        attempt = ledger.acquire(100 + cursor)
        assert attempt is not None
        ledger.finish(attempt, 100 + cursor, "rejected", http_status=401)
    ledger.observe_page(MAX_WAKE_RECEIPTS, MAX_WAKE_RECEIPTS + 1, sources=("handoffs",))
    with pytest.raises(WakeLedgerError, match="receipt capacity reached"):
        ledger.acquire(300)
    assert len(ledger.receipts()) == MAX_WAKE_RECEIPTS
    assert ledger.summary()["unresolved_failures"] == MAX_WAKE_RECEIPTS
    assert ledger.summary()["pending_sources"] == 4
    assert ledger.control_delivery("retire", first.delivery_id, 1, 301) == "retired"
    resumed = ledger.acquire(302)
    assert resumed is not None
    assert ledger.summary()["retired_receipts"] == 1
    assert ledger.summary()["unresolved_failures"] == MAX_WAKE_RECEIPTS - 1
    assert len(ledger.receipts()) == MAX_WAKE_RECEIPTS


@pytest.mark.parametrize("action", ["redrive", "acquire"])
def test_exhausted_lifetime_fence_refuses_instead_of_repeating(tmp_path: Path, action: str) -> None:
    _, ledger, first = operator_ledger(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE deliveries SET attempt=?,cycle_base=?", (MAX_WAKE_INTEGER, MAX_WAKE_INTEGER - 1)
        )
        if action == "acquire":
            db.execute("UPDATE deliveries SET state='retry'")
    with pytest.raises(WakeLedgerError, match="attempt counter exhausted"):
        if action == "redrive":
            ledger.control_delivery("redrive", first.delivery_id, MAX_WAKE_INTEGER, 200)
        else:
            ledger.acquire(200)


@pytest.mark.parametrize(
    "action,identifier,fence",
    [
        ("other", "msg_" + "a" * 32, 1),
        ("redrive", "bad-id", 1),
        ("redrive", None, 1),
        ("retire", "msg_" + "a" * 32, True),
        ("redrive", "msg_" + "a" * 32, 0),
        ("redrive", "msg_" + "a" * 32, MAX_WAKE_INTEGER + 1),
    ],
)
def test_invalid_control_inputs_do_not_create_a_ledger(
    tmp_path: Path,
    action: Any,
    identifier: Any,
    fence: Any,
) -> None:
    _, ledger, _ = operator_ledger(tmp_path)
    absent = WakeLedger(tmp_path / "absent", ledger.scope)
    with pytest.raises(WakeLedgerError, match="invalid wake control request"):
        absent.control_delivery(action, identifier, fence, 100)
    assert not absent.path.exists()


@pytest.mark.parametrize(
    "damage", ["missing", "oversize", "foreign-binding", "corrupt-hub", "corrupt-db", "disabled"]
)
def test_local_operator_status_refuses_unavailable_or_foreign_state(
    tmp_path: Path, damage: str
) -> None:
    path, ledger, _ = operator_ledger(tmp_path)
    if damage == "missing":
        ledger.path.unlink()
    elif damage == "oversize":
        with ledger.path.open("r+b") as stream:
            stream.truncate(4 * 1024**2 + 1)
    elif damage == "corrupt-db":
        ledger.path.write_bytes(b"private error sentinel")
    elif damage == "disabled":
        policy = json.loads(path.read_text())
        policy["subjects"]["alice"]["enabled"] = False
        path.write_text(json.dumps(policy))
    else:
        with sqlite3.connect(ledger.path) as db:
            if damage == "foreign-binding":
                db.execute("UPDATE meta SET scope='foreign'")
            else:
                db.execute("UPDATE meta SET hub=''")
    with pytest.raises(WakeLedgerError):
        WakeControl(path, "alice", "ALPHA").status()
    if damage == "missing":
        assert not ledger.path.exists()


def test_an_unreadable_grant_file_is_the_same_refusal_as_missing_state(tmp_path: Path) -> None:
    """The opener promises one error class; a grant file that cannot be read keeps it."""
    path, _ledger, _attempt = operator_ledger(tmp_path)
    path.write_text("{not a policy")
    with pytest.raises(WakeLedgerError, match="wake control state unavailable"):
        WakeControl(path, "alice", "ALPHA").status()
