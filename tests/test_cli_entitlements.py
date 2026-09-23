# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — entitlement CLI process journeys
"""Exercise persistent account events through the installed CLI entry point."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.compute_credit import approval_subject
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.persistence import EventStore


def _run(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "entitlements", *args, "--store", str(store)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _record(
    store: Path, event: dict[str, object], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    source = tmp_path / f"{event['event_id']}.json"
    source.write_text(json.dumps(event), encoding="utf-8")
    source.chmod(0o600)
    return _run(store, "record", "--file", str(source))


def _event(kind: str, event_id: str, **fields: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "kind": kind,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": "operator:owner",
        "confidence": "operator",
        **fields,
    }


def test_real_cli_persists_shared_pool_without_double_count(tmp_path: Path) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    now = datetime.now(timezone.utc)
    records = [
        _event(
            "account", "a1", account_id="account-1", label="Confidential account", status="active"
        ),
        _event("pool", "p1", pool_id="pool-1", account_id="account-1", unit="tokens"),
        _event(
            "surface",
            "s1",
            surface_id="chat",
            account_id="account-1",
            pool_id="pool-1",
            product="Chat",
            channel="chat",
        ),
        _event(
            "surface",
            "s2",
            surface_id="code",
            account_id="account-1",
            pool_id="pool-1",
            product="Coding CLI",
            channel="coding_cli",
        ),
        _event(
            "window",
            "w1",
            window_id="window-1",
            pool_id="pool-1",
            starts_at=(now - timedelta(days=1)).isoformat(),
            ends_at=(now + timedelta(days=1)).isoformat(),
            grant="1000",
            unit="tokens",
            price_revision="rev-1",
        ),
        _event(
            "usage",
            "u1",
            window_id="window-1",
            window_event_id="w1",
            source_event_id="host-1",
            amount="100",
            observed_at=(now - timedelta(hours=1)).isoformat(),
        ),
    ]
    for record in records:
        assert _record(store, record, tmp_path).returncode == 0
    assert "already recorded" in _record(store, records[-1], tmp_path).stdout
    shown = _run(store, "show")
    assert shown.returncode == 0, shown.stderr
    report = json.loads(shown.stdout)
    assert report["account_count"] == 1
    assert report["pool_count"] == 1
    assert report["accounts"][0]["label"] == "Confidential account"
    pool = report["pools"][0]
    assert len(pool["surfaces"]) == 2
    assert pool["windows"][0]["remaining"] == "900"
    assert pool["windows"][0]["balance_evidence"] == "incomplete_usage_estimate"
    history = _run(store, "history")
    assert history.returncode == 0
    assert len(json.loads(history.stdout)["events"]) == len(records)


def test_real_cli_rejects_corrupt_and_world_readable_input(tmp_path: Path) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    invalid.chmod(0o600)
    assert _run(store, "record", "--file", str(invalid)).returncode == 2
    invalid.write_text(
        json.dumps(_event("account", "a1", account_id="a", label="Private", status="active")),
        encoding="utf-8",
    )
    invalid.chmod(0o644)
    denied = _run(store, "record", "--file", str(invalid))
    assert denied.returncode == 2
    assert "owner-only" in denied.stderr
    assert not store.exists()


@pytest.mark.parametrize(
    ("resource_kind", "unit", "capability"),
    [
        ("gpu_time", "gpu_seconds", "cuda"),
        ("quantum_shots", "shots", "qpu"),
        ("quantum_credits", "quantum_credits", "quantum-simulator"),
        ("ci_minutes", "ci_minutes", "ci-runner"),
        ("cloud_grant", "USD", "cloud-compute"),
    ],
)
def test_real_cli_compute_credit_reset_approval_and_suspension(
    tmp_path: Path, resource_kind: str, unit: str, capability: str
) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    now = datetime.now(timezone.utc)
    first_start = now - timedelta(days=2)
    reset = now - timedelta(hours=1)
    second_end = now + timedelta(days=1)
    records = [
        _event("account", "a1", account_id="a", label="Private compute grant", status="active"),
        _event(
            "pool",
            "p1",
            pool_id="p",
            account_id="a",
            unit=unit,
            resource_kind=resource_kind,
            capabilities=[capability],
            data_classes=["internal"],
            eligible_projects=["SYNAPSE-CHANNEL"],
            idle_cost={"amount_per_hour": "0.40", "currency": "USD"},
        ),
        _event(
            "window",
            "w1",
            window_id="old",
            pool_id="p",
            starts_at=first_start.isoformat(),
            ends_at=reset.isoformat(),
            grant="100",
            unit=unit,
            price_revision="r1",
        ),
        _event(
            "window",
            "w2",
            window_id="new",
            pool_id="p",
            starts_at=reset.isoformat(),
            ends_at=second_end.isoformat(),
            grant="100",
            unit=unit,
            price_revision="r2",
        ),
        _event(
            "balance",
            "b1",
            window_id="new",
            window_event_id="w2",
            source_event_id="snapshot-1",
            remaining="80",
            observed_at=(now - timedelta(minutes=30)).isoformat(),
        ),
        _event(
            "usage",
            "u1",
            window_id="new",
            window_event_id="w2",
            source_event_id="job-1",
            amount="10",
            observed_at=(now - timedelta(minutes=15)).isoformat(),
        ),
    ]
    for record in records:
        result = _record(store, record, tmp_path)
        assert result.returncode == 0, result.stderr
    shown = json.loads(_run(store, "show").stdout)
    current = [row for row in shown["pools"][0]["windows"] if row["current"]]
    assert len(current) == 1 and current[0]["window_id"] == "new"
    assert current[0]["remaining"] == "70"
    assert shown["pools"][0]["idle_cost"]["currency"] == "USD"
    assert current[0]["expires_in_seconds"] > 0
    task = {
        "task_id": "TASK-1",
        "project": "SYNAPSE-CHANNEL",
        "resource_kind": resource_kind,
        "capability": capability,
        "data_class": "internal",
        "unit": unit,
        "required_amount": "30",
        "estimated_total_cost": "2",
        "max_total_cost": "3",
        "cost_currency": "USD",
        "price_revision": "r2",
        "priority": 1,
        "board_version": 1,
        "authorisation_expires_at": second_end.isoformat(),
    }
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
    task_file.chmod(0o600)
    subject_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "entitlements",
            "compute-subjects",
            "--file",
            str(task_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert subject_result.returncode == 0, subject_result.stderr
    subject = json.loads(subject_result.stdout)["subjects"]["TASK-1"]
    approvals = EventStore(hub)
    board = Blackboard()
    accepted, _ = board.post_task(
        task_id="TASK-1",
        title="Approved compute work",
        author="operator",
        project="SYNAPSE-CHANNEL",
        now=now.timestamp(),
    )
    assert accepted
    approvals.append(EventKind.LEDGER_TASK, board.tasks["TASK-1"].as_dict(), durable=True)
    for state, author in (("requested", "SYNAPSE-CHANNEL/codex"), ("approved", "CEO/claude")):
        approvals.append(
            EventKind.LEDGER_PROGRESS,
            {
                "author": author,
                "kind": APPROVAL_NOTE_KIND,
                "task_id": subject,
                "text": format_approval_note(subject=subject, state=state),
            },
            durable=True,
        )
    approvals.close()
    suggest = _run(
        store,
        "suggest-compute",
        "--file",
        str(task_file),
        "--hub-db",
        str(hub),
        "--reviewer",
        "CEO/claude",
    )
    assert suggest.returncode == 0, suggest.stderr
    result = json.loads(suggest.stdout)
    assert result["suggestions"][0]["options"][0]["remaining"] == "70"
    assert result["no_job_launched"] is True
    invalid_pool = _event(
        "pool",
        "p-invalid",
        pool_id="other",
        account_id="a",
        unit="tokens",
        resource_kind=resource_kind,
        capabilities=[capability],
        data_classes=["internal"],
        eligible_projects=["SYNAPSE-CHANNEL"],
    )
    rejected_unit = _record(store, invalid_pool, tmp_path)
    assert rejected_unit.returncode == 2
    assert "incompatible" in rejected_unit.stderr
    restricted = {**task, "data_class": "restricted"}
    task_file.write_text(json.dumps({"tasks": [restricted]}), encoding="utf-8")
    restricted_subject = approval_subject(restricted)
    approvals = EventStore(hub)
    approvals.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "CEO/claude",
            "kind": APPROVAL_NOTE_KIND,
            "task_id": restricted_subject,
            "text": format_approval_note(subject=restricted_subject, state="approved"),
        },
        durable=True,
    )
    approvals.close()
    restricted_result = _run(
        store,
        "suggest-compute",
        "--file",
        str(task_file),
        "--hub-db",
        str(hub),
        "--reviewer",
        "CEO/claude",
    )
    assert restricted_result.returncode == 0, restricted_result.stderr
    assert json.loads(restricted_result.stdout)["excluded"] == [
        {"task_id": "TASK-1", "reason": "no_current_eligible_pool"}
    ]
    task_file.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
    correction = {
        **records[0],
        "event_id": "a2",
        "recorded_at": (now + timedelta(seconds=1)).isoformat(),
        "status": "suspended",
        "supersedes": "a1",
    }
    assert _record(store, correction, tmp_path).returncode == 0
    refused = json.loads(
        _run(
            store,
            "suggest-compute",
            "--file",
            str(task_file),
            "--hub-db",
            str(hub),
            "--reviewer",
            "CEO/claude",
        ).stdout
    )
    assert refused["suggestions"] == []
    assert refused["excluded"][0]["reason"] == "no_current_eligible_pool"


def test_real_cli_correction_and_suspended_account(tmp_path: Path) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    account = _event("account", "a1", account_id="account-1", label="Private", status="active")
    assert _record(store, account, tmp_path).returncode == 0
    assert (
        _record(
            store,
            _event("pool", "p1", pool_id="pool-1", account_id="account-1", unit="USD"),
            tmp_path,
        ).returncode
        == 0
    )
    correction = {
        **account,
        "event_id": "a2",
        "recorded_at": (
            datetime.fromisoformat(str(account["recorded_at"])) + timedelta(seconds=1)
        ).isoformat(),
        "source": "operator:manual-correction",
        "status": "suspended",
        "supersedes": "a1",
    }
    assert _record(store, correction, tmp_path).returncode == 0
    report = json.loads(_run(store, "show").stdout)
    assert report["accounts"][0]["status"] == "suspended"
    assert report["pools"][0]["account_usable"] is False
    history = json.loads(_run(store, "history").stdout)
    assert [item["status"] for item in history["events"] if item["kind"] == "account"] == [
        "active",
        "suspended",
    ]


def test_real_cli_imports_only_official_ollama_counts_once(tmp_path: Path) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    now = datetime.now(timezone.utc)
    for record in (
        _event("account", "a1", account_id="a", label="Private", status="active"),
        _event("pool", "p1", pool_id="p", account_id="a", unit="tokens"),
        _event(
            "window",
            "w1",
            window_id="w",
            pool_id="p",
            starts_at=(now - timedelta(days=1)).isoformat(),
            ends_at=(now + timedelta(days=1)).isoformat(),
            grant="1000",
            unit="tokens",
            price_revision="r1",
        ),
    ):
        assert _record(store, record, tmp_path).returncode == 0
    response = tmp_path / "ollama.json"
    response.write_text(
        json.dumps(
            {
                "model": "gemma3:1b",
                "created_at": now.isoformat(),
                "done": True,
                "response": "private generated content",
                "prompt_eval_count": 12,
                "eval_count": 4,
            }
        ),
        encoding="utf-8",
    )
    response.chmod(0o600)
    args = (
        "observe-ollama",
        "--file",
        str(response),
        "--window-id",
        "w",
        "--window-event-id",
        "w1",
    )
    imported = _run(store, *args)
    assert imported.returncode == 0, imported.stderr
    assert "already recorded" in _run(store, *args).stdout
    history = _run(store, "history")
    assert history.returncode == 0
    assert "private generated content" not in history.stdout
    usage = json.loads(history.stdout)["events"][-1]
    assert usage["amount"] == "16"
    assert usage["confidence"] == "official"
    report = json.loads(_run(store, "show").stdout)
    assert report["pools"][0]["windows"][0]["remaining"] == "984"
    wrong_window = _run(
        store,
        "observe-ollama",
        "--file",
        str(response),
        "--window-id",
        "different",
        "--window-event-id",
        "w1",
    )
    assert wrong_window.returncode == 2
    assert "conflicts" in wrong_window.stderr


def test_real_cli_refuses_partial_or_invalid_ollama_counts(tmp_path: Path) -> None:
    store = tmp_path / "private" / "ledger.sqlite3"
    response = tmp_path / "ollama.json"
    for record in (
        {"done": False, "response": "partial", "prompt_eval_count": 2, "eval_count": 1},
        {"done": True, "response": "final", "prompt_eval_count": True, "eval_count": 1},
        {"done": True, "response": "final", "prompt_eval_count": 2},
    ):
        response.write_text(
            json.dumps(
                {
                    "model": "gemma3:1b",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **record,
                }
            ),
            encoding="utf-8",
        )
        response.chmod(0o600)
        result = _run(
            store,
            "observe-ollama",
            "--file",
            str(response),
            "--window-id",
            "w",
            "--window-event-id",
            "w1",
        )
        assert result.returncode == 2
    assert not store.exists()
