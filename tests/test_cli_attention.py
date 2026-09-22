# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — package CLI attention journey
"""Exercise the packaged attention CLI against durable hub events."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.entitlement_store import append_event
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "attention", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _notifier_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    capture = tmp_path / "preview.txt"
    binary = tmp_path / "bin"
    binary.mkdir()
    notifier = binary / "notify-send"
    notifier.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$ATTENTION_NOTIFY_CAPTURE"\n')
    notifier.chmod(0o700)
    return (
        {
            **os.environ,
            "PATH": f"{binary}:{os.environ.get('PATH', '')}",
            "ATTENTION_NOTIFY_CAPTURE": str(capture),
        },
        capture,
    )


def test_cli_observes_approval_then_explicitly_resolves_without_deciding(tmp_path: Path) -> None:
    hub_path = tmp_path / "hub.db"
    queue = tmp_path / "attention" / "queue.db"
    hub = EventStore(hub_path)
    try:
        hub.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(
                    subject="task-1", state="requested", reason="private task body"
                ),
            },
            durable=True,
        )
        synced = _cli(
            "sync", str(hub_path), "--store", str(queue), "--approval-hours", "0.00000001"
        )
        assert synced.returncode == 0, synced.stderr
        assert json.loads(synced.stdout)["synced"]["hub"]["created"] == 1
        listing = _cli("list", "--store", str(queue), "--json")
        assert listing.returncode == 0, listing.stderr
        alerts = json.loads(listing.stdout)["alerts"]
        assert len(alerts) == 1 and alerts[0]["state"] == "expired"
        assert "private task body" not in listing.stdout
        duplicate = _cli(
            "sync", str(hub_path), "--store", str(queue), "--approval-hours", "0.00000001"
        )
        assert json.loads(duplicate.stdout)["synced"]["hub"]["unchanged"] == 1
        resolved = _cli("resolve", "approval:task-1", "--store", str(queue))
        assert resolved.returncode == 0, resolved.stderr
        assert json.loads(_cli("list", "--store", str(queue), "--json").stdout)["alerts"] == []
        # A local attention action cannot mutate the independent approval ledger.
        assert len(tuple(hub.iter_events(kinds={EventKind.LEDGER_PROGRESS}))) == 1
    finally:
        hub.close()


def test_desktop_preview_is_generic_and_suppressed_after_first_delivery(tmp_path: Path) -> None:
    hub_path = tmp_path / "hub.db"
    queue = tmp_path / "attention" / "queue.db"
    env, capture = _notifier_env(tmp_path)
    hub = EventStore(hub_path)
    try:
        hub.append(
            EventKind.LEDGER_PROGRESS,
            {
                "kind": "approval",
                "author": "operator:one",
                "text": format_approval_note(
                    subject="sensitive-task", state="requested", reason="secret body"
                ),
            },
            durable=True,
        )
        args = ("sync", str(hub_path), "--store", str(queue), "--desktop")
        first = _cli(*args, env=env)
        second = _cli(*args, env=env)
        assert first.returncode == 0 and second.returncode == 0
        assert json.loads(first.stdout)["desktop_previews"] == 1
        assert json.loads(second.stdout)["desktop_previews"] == 0
        preview = capture.read_text()
        assert preview.count("SYNAPSE attention") == 1
        assert "sensitive-task" not in preview and "secret body" not in preview
    finally:
        hub.close()


def test_cli_projects_private_quota_without_previewing_account_name(tmp_path: Path) -> None:
    hub_path = tmp_path / "hub.db"
    EventStore(hub_path).close()
    queue = tmp_path / "attention" / "queue.db"
    ledger = tmp_path / "entitlements" / "ledger.db"
    now = datetime.now(timezone.utc)
    common = {
        "recorded_at": (now - timedelta(days=2)).isoformat(),
        "source": "operator:owner",
        "confidence": "operator",
    }
    entries: list[dict[str, object]] = [
        {
            **common,
            "event_id": "a1",
            "kind": "account",
            "account_id": "a1",
            "label": "Private subscription",
            "status": "active",
        },
        {
            **common,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "p1",
            "account_id": "a1",
            "unit": "tokens",
        },
        {
            **common,
            "event_id": "w1",
            "kind": "window",
            "window_id": "w1",
            "pool_id": "p1",
            "starts_at": (now - timedelta(days=3)).isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat(),
            "renewal_at": (now + timedelta(days=1)).isoformat(),
            "grant": "100",
            "unit": "tokens",
            "price_revision": "price-1",
        },
        {
            **common,
            "event_id": "b1",
            "kind": "balance",
            "window_id": "w1",
            "window_event_id": "w1",
            "source_event_id": "balance-source-1",
            "remaining": "0",
            "observed_at": (now - timedelta(days=1)).isoformat(),
        },
    ]
    for entry in entries:
        append_event(ledger, entry)
    env, capture = _notifier_env(tmp_path)
    result = _cli(
        "sync",
        str(hub_path),
        "--entitlement-store",
        str(ledger),
        "--store",
        str(queue),
        "--desktop",
        "--stale-hours",
        "0.1",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["synced"]["quota"]["created"] == 2
    report = json.loads(_cli("list", "--store", str(queue), "--json").stdout)
    assert {row["kind"] for row in report["alerts"]} == {"stale_data", "quota_reset"}
    assert "Private subscription" not in str(report)
    assert "Private subscription" not in capture.read_text()
