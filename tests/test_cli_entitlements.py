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
