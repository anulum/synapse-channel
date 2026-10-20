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

import pytest

from synapse_channel.core.approvals import format_approval_note
from synapse_channel.core.entitlement_store import append_event
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _pending_approvals(path: Path) -> None:
    with EventStore(path) as hub:
        for subject in ("first", "second"):
            hub.append(
                EventKind.LEDGER_PROGRESS,
                {
                    "kind": "approval",
                    "author": "operator:one",
                    "text": format_approval_note(
                        subject=subject, state="requested", reason="private request"
                    ),
                },
                durable=True,
            )


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        (("--approval-hours", "0"), "approval deadline"),
        (("--stale-hours", "8761"), "stale-data age"),
        (("--review-store", "absent"), "exact reviewer seat"),
        (
            ("--review-store", "absent", "--reviewer-seat", "P/reviewer"),
            "review evidence store is missing",
        ),
        (("--entitlement-store", "absent"), "missing entitlement ledger"),
    ],
)
def test_sync_rejects_unavailable_sources_and_invalid_ages_without_a_queue(
    tmp_path: Path, options: tuple[str, ...], reason: str
) -> None:
    hub = tmp_path / "hub.db"
    EventStore(hub).close()
    queue = tmp_path / "queue.db"
    result = _cli("sync", str(hub), "--store", str(queue), *options)
    assert result.returncode == 2
    assert reason in result.stderr
    assert not queue.exists()


def test_sync_missing_hub_does_not_create_it(tmp_path: Path) -> None:
    hub = tmp_path / "missing.db"
    result = _cli("sync", str(hub), "--store", str(tmp_path / "queue.db"))
    assert result.returncode == 2
    assert "missing hub event store" in result.stderr
    assert not hub.exists()


def test_default_queue_empty_text_view_does_not_invent_alerts(tmp_path: Path) -> None:
    result = _cli("list", env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")})
    assert result.returncode == 0, result.stderr
    assert "0 active" in result.stdout
    assert "more;" not in result.stdout


def test_text_listing_is_bounded_and_snoozing_preserves_hub_evidence(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    queue = tmp_path / "queue.db"
    _pending_approvals(hub)
    assert (
        _cli("sync", str(hub), "--store", str(queue), "--approval-hours", "0.00000001").returncode
        == 0
    )
    listing = _cli("list", "--store", str(queue), "--max-items", "1")
    assert listing.returncode == 0, listing.stderr
    assert "2 active" in listing.stdout
    assert "+1 more; increase --max-items" in listing.stdout
    assert listing.stdout.count("[expired]") == 1
    assert "private request" not in listing.stdout
    snoozed = _cli("snooze", "approval:first", "--store", str(queue), "--hours", "1")
    assert snoozed.returncode == 0, snoozed.stderr
    assert json.loads(snoozed.stdout)["snoozed_until"] is not None
    with EventStore(hub) as observed:
        assert len(tuple(observed.read_all())) == 2
    rejected = _cli("list", "--store", str(queue), "--max-items", "0")
    assert rejected.returncode == 2 and "max-items" in rejected.stderr


def test_desktop_failure_never_marks_a_preview_as_delivered(tmp_path: Path) -> None:
    hub = tmp_path / "hub.db"
    queue = tmp_path / "queue.db"
    _pending_approvals(hub)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    env = {**os.environ, "PATH": str(empty_bin)}
    args = ("sync", str(hub), "--store", str(queue), "--desktop")
    missing = _cli(*args, env=env)
    assert missing.returncode == 2 and "notify-send is unavailable" in missing.stderr
    invalid = _cli(*args, "--desktop-max", "0", env=env)
    assert invalid.returncode == 2 and "between 1 and 50" in invalid.stderr
    notifier = empty_bin / "notify-send"
    notifier.write_text("#!/bin/sh\nexit 7\n", encoding="ascii")
    notifier.chmod(0o700)
    failed = _cli(*args, env=env)
    assert failed.returncode == 2
    assert "exit status 7" in failed.stderr
    rows = json.loads(_cli("list", "--store", str(queue), "--json").stdout)["alerts"]
    assert len(rows) == 2
    assert all(
        row["notified_revision"] != f"{row['source_revision']}:{row['state']}" for row in rows
    )


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
