# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local attention queue commands
"""Observe real alert sources and explicitly manage local attention state."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3

# The only subprocess is a fixed argv for the locally resolved notification program.
import subprocess  # nosec B404
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel.core.attention import (
    ATTENTION_EVENT_KINDS,
    project_hub_attention,
    project_quota_attention,
)
from synapse_channel.core.attention_store import (
    AttentionStoreError,
    default_attention_store,
    mark_notified,
    queue_view,
    set_alert_state,
    sync_evidence,
)
from synapse_channel.core.entitlement_store import EntitlementStoreError, read_events
from synapse_channel.core.persistence import EventStore


def _store(args: argparse.Namespace) -> Path:
    return Path(args.store).expanduser() if args.store else default_attention_store()


def _bounded_positive(value: float, name: str) -> float:
    if not 0 < value <= 8760:
        raise AttentionStoreError(f"{name} must be positive and at most 8760 hours")
    return value


def _notify(path: Path, *, now: float, maximum: int) -> int:
    if not 1 <= maximum <= 50:
        raise AttentionStoreError("desktop maximum must be between 1 and 50")
    view = queue_view(path, now=now)
    eligible = [
        row
        for row in view["alerts"]
        if row["notified_revision"] != f"{row['source_revision']}:{row['state']}"
    ][:maximum]
    if not eligible:
        return 0
    executable = shutil.which("notify-send")
    if executable is None:
        raise AttentionStoreError("notify-send is unavailable")
    # Desktop previews stay generic even when the owner queue contains opaque IDs.
    # The resolved executable receives no source content or user-controlled arguments.
    subprocess.run(  # nosec B603
        [
            executable,
            "SYNAPSE attention",
            f"{len(eligible)} item(s) need review in the local queue",
        ],
        check=True,
        timeout=5,
    )
    return mark_notified(path, [str(row["key"]) for row in eligible], now=now)


def _sync(args: argparse.Namespace) -> int:
    """Reconcile a real hub event store and optional owner-local entitlement ledger."""
    now = time.time()
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise AttentionStoreError(f"missing hub event store: {db_path}")
    approval_seconds = _bounded_positive(args.approval_hours, "approval deadline") * 3600
    stale_seconds = _bounded_positive(args.stale_hours, "stale-data age") * 3600
    quota = None
    if args.entitlement_store:
        ledger = Path(args.entitlement_store).expanduser()
        if not ledger.is_file():
            raise AttentionStoreError(f"missing entitlement ledger: {ledger}")
        quota = project_quota_attention(
            read_events(ledger),
            at=datetime.fromtimestamp(now, tz=timezone.utc),
            stale_after_seconds=stale_seconds,
        )
    hub = EventStore(db_path, key_file=args.db_key_file)
    try:
        hub_evidence = project_hub_attention(
            hub.iter_events(kinds=ATTENTION_EVENT_KINDS),
            now=now,
            approval_ttl_seconds=approval_seconds,
        )
    finally:
        hub.close()
    store = _store(args)
    counts = {
        "hub": sync_evidence(
            store,
            source="hub",
            evidence=hub_evidence,
            now=now,
        )
    }
    if quota is not None:
        counts["quota"] = sync_evidence(store, source="quota", evidence=quota, now=now)
    notified = _notify(store, now=now, maximum=args.desktop_max) if args.desktop else 0
    print(json.dumps({"synced": counts, "desktop_previews": notified}, sort_keys=True))
    return 0


def _list(args: argparse.Namespace) -> int:
    """Show a bounded queue snapshot and explicit observer freshness."""
    view = queue_view(_store(args), now=time.time(), observer_age_limit=args.observer_seconds)
    maximum = args.max_items
    if not 1 <= maximum <= 500:
        raise AttentionStoreError("max-items must be between 1 and 500")
    rows = view["alerts"]
    report = {**view, "alerts": rows[:maximum], "remaining": max(0, len(rows) - maximum)}
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"Attention: {report['state']} ({len(rows)} active, {report['snoozed_count']} snoozed)"
        )
        for row in report["alerts"]:
            print(f"{row['severity']} {row['key']} [{row['state']}]: {row['action']}")
        if report["remaining"]:
            print(f"+{report['remaining']} more; increase --max-items")
    return 0


def _transition(args: argparse.Namespace) -> int:
    """Record an explicit local snooze or resolution without touching hub gates."""
    now = time.time()
    until = (
        now + _bounded_positive(args.hours, "snooze duration") * 3600
        if args.command == "snooze"
        else None
    )
    result = set_alert_state(_store(args), args.key, action=args.command, now=now, until=until)
    print(json.dumps({"key": result["key"], "state": result["state"], "snoozed_until": until}))
    return 0


def _run(args: argparse.Namespace) -> int:
    try:
        return int(args.action(args))
    except (
        AttentionStoreError,
        EntitlementStoreError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"attention: {exc}", file=sys.stderr)
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the owner-local ``attention`` CLI group."""
    parser = subparsers.add_parser("attention", help="Observe and manage local attention alerts.")
    group = parser.add_subparsers(dest="command", required=True)

    sync = group.add_parser(
        "sync", help="Project real hub and optional quota events into the queue."
    )
    sync.add_argument("db", help="Hub SQLite event store.")
    sync.add_argument("--db-key-file", default=None, help="Owner-only SQLCipher key file.")
    sync.add_argument("--entitlement-store", default=None, help="Owner-only C04 ledger.")
    sync.add_argument("--approval-hours", type=float, default=24.0)
    sync.add_argument("--stale-hours", type=float, default=24.0)
    sync.add_argument("--desktop", action="store_true", help="Send generic local desktop previews.")
    sync.add_argument("--desktop-max", type=int, default=10)
    sync.add_argument("--store", default=None, help="Owner-local queue SQLite path.")
    sync.set_defaults(action=_sync, func=_run)

    listing = group.add_parser("list", help="Show active alerts and observer freshness.")
    listing.add_argument("--store", default=None)
    listing.add_argument("--observer-seconds", type=float, default=300.0)
    listing.add_argument("--max-items", type=int, default=50)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(action=_list, func=_run)

    for command in ("snooze", "resolve"):
        transition = group.add_parser(command, help=f"Explicitly {command} one local alert.")
        transition.add_argument("key", help="Exact alert key from attention list.")
        transition.add_argument("--store", default=None)
        if command == "snooze":
            transition.add_argument("--hours", type=float, default=1.0)
        transition.set_defaults(action=_transition, func=_run)
