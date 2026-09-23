# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — owner-local attention lifecycle storage
"""Persist source-backed alerts, observer freshness, snooze and resolution."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from synapse_channel.core.attention import AttentionEvidence
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)


class AttentionStoreError(SynapseError, ValueError):
    """An alert transition or owner-local store is invalid."""

    code = "attention_store"


def default_attention_store() -> Path:
    """Return the owner-local queue path outside the shared hub database."""
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local" / "state"
    return root / "synapse-channel" / "attention" / "queue.sqlite3"


def _open(path: Path) -> sqlite3.Connection:
    parent = path.parent
    try:
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True)
            apply_owner_only_dir(parent)
        assert_owner_only_dir_path(parent, purpose="attention directory")
        if path.is_symlink():
            raise AttentionStoreError("attention store must not be a symlink")
        existed = path.exists()
        if existed:
            assert_owner_only_file_path(path, purpose="attention store")
        db = sqlite3.connect(path, timeout=5.0)
        db.row_factory = sqlite3.Row
        if not existed:
            apply_owner_only_file(path)
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0 and not existed:
            db.executescript(
                "CREATE TABLE alerts ("
                "key TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, "
                "subject TEXT NOT NULL, severity TEXT NOT NULL, state TEXT NOT NULL, "
                "action TEXT NOT NULL, source_revision TEXT NOT NULL, "
                "observed_at REAL NOT NULL, expires_at REAL, updated_at REAL NOT NULL, "
                "snoozed_until REAL, notified_revision TEXT);"
                "CREATE TABLE observers (source TEXT PRIMARY KEY, last_success REAL NOT NULL);"
                "PRAGMA user_version=1;"
            )
        elif version != 1:
            raise AttentionStoreError(f"unsupported attention store version {version}")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AttentionStoreError("attention store integrity check failed")
        return db
    except AttentionStoreError:
        if "db" in locals():
            db.close()
        raise
    except (OSError, sqlite3.DatabaseError, SecurePathError) as exc:
        if "db" in locals():
            db.close()
        raise AttentionStoreError(f"cannot open private attention store: {exc}") from exc


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def sync_evidence(
    path: Path, *, source: str, evidence: Sequence[AttentionEvidence], now: float
) -> dict[str, int]:
    """Reconcile one complete, successfully observed source snapshot atomically.

    Parameters
    ----------
    path : pathlib.Path
        Owner-local queue database.
    source : str
        ``hub`` or ``quota``; each source is reconciled independently.
    evidence : Sequence[AttentionEvidence]
        Latest source-backed alert candidates and explicit source resolutions.
    now : float
        Wall-clock timestamp of this successful observer pass.

    Returns
    -------
    dict[str, int]
        Counts of created, changed and unchanged alerts.

    Raises
    ------
    AttentionStoreError
        If evidence is duplicated or the source is unsupported.
    """
    if not math.isfinite(now) or now <= 0:
        raise AttentionStoreError("observer time must be finite and positive")
    if source not in {"hub", "quota", "review"}:
        raise AttentionStoreError("attention source must be hub, quota or review")
    keys = [item.key for item in evidence]
    if len(keys) != len(set(keys)):
        raise AttentionStoreError("attention evidence contains duplicate keys")
    for item in evidence:
        if (
            not item.key
            or len(item.key) > 256
            or item.severity not in {"critical", "warning", "info"}
            or item.state not in {"open", "expired", "resolved"}
            or not math.isfinite(item.observed_at)
            or (item.expires_at is not None and not math.isfinite(item.expires_at))
        ):
            raise AttentionStoreError("attention evidence is invalid")
    counts = {"created": 0, "changed": 0, "unchanged": 0}
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = {
            str(row["key"]): row
            for row in db.execute("SELECT * FROM alerts WHERE source=?", (source,))
        }
        for item in evidence:
            previous = existing.get(item.key)
            if previous is None:
                if item.state == "resolved":
                    continue
                db.execute(
                    "INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.key,
                        source,
                        item.kind,
                        item.subject,
                        item.severity,
                        item.state,
                        item.action,
                        item.source_revision,
                        item.observed_at,
                        item.expires_at,
                        now,
                        None,
                        None,
                    ),
                )
                counts["created"] += 1
                continue
            revision_changed = previous["source_revision"] != item.source_revision
            if not revision_changed and previous["state"] == "resolved":
                counts["unchanged"] += 1
                continue
            if not revision_changed and previous["kind"] == "recovery":
                counts["unchanged"] += 1
                continue
            state = item.state
            expiry = item.expires_at
            if item.kind == "recovery" and previous["kind"] == "failed_delivery":
                state = "open"
                expiry = now + 86400.0
            if not revision_changed and previous["state"] == state:
                counts["unchanged"] += 1
                continue
            db.execute(
                "UPDATE alerts SET kind=?, subject=?, severity=?, state=?, action=?, "
                "source_revision=?, observed_at=?, expires_at=?, updated_at=?, "
                "snoozed_until=?, notified_revision=? WHERE key=?",
                (
                    item.kind,
                    item.subject,
                    item.severity,
                    state,
                    item.action,
                    item.source_revision,
                    item.observed_at,
                    expiry,
                    now,
                    None if revision_changed else previous["snoozed_until"],
                    (
                        None
                        if revision_changed or previous["state"] != state
                        else previous["notified_revision"]
                    ),
                    item.key,
                ),
            )
            counts["changed"] += 1
        if source == "quota":
            for key, previous in existing.items():
                if key not in keys and previous["state"] not in {"resolved", "expired"}:
                    db.execute(
                        "UPDATE alerts SET state='resolved', updated_at=? WHERE key=?",
                        (now, key),
                    )
                    counts["changed"] += 1
        db.execute(
            "INSERT INTO observers(source,last_success) VALUES(?,?) "
            "ON CONFLICT(source) DO UPDATE SET last_success=excluded.last_success",
            (source, now),
        )
    return counts


def queue_view(path: Path, *, now: float, observer_age_limit: float = 300.0) -> dict[str, Any]:
    """Return active alerts and distinguish quiet observation from missing observation.

    Parameters
    ----------
    path : pathlib.Path
        Owner-local queue database.
    now : float
        Wall-clock evaluation timestamp.
    observer_age_limit : float
        Maximum time since a successful source observation.

    Returns
    -------
    dict[str, Any]
        Active queue, source freshness and ``quiet`` or ``missing_observer`` state.
    """
    if not math.isfinite(now) or not math.isfinite(observer_age_limit) or observer_age_limit <= 0:
        raise AttentionStoreError("observer age limit must be positive")
    with closing(_open(path)) as db, db:
        rows = [_row(row) for row in db.execute("SELECT * FROM alerts ORDER BY key")]
        observers = {
            str(row["source"]): float(row["last_success"])
            for row in db.execute("SELECT source,last_success FROM observers")
        }
    active = [
        row
        for row in rows
        if row["state"] in {"open", "expired"}
        and (row["expires_at"] is None or row["expires_at"] > now or row["kind"] == "approval")
        and (row["snoozed_until"] is None or row["snoozed_until"] <= now)
    ]
    missing = not observers or any(
        last > now + 30 or now - last > observer_age_limit for last in observers.values()
    )
    return {
        "state": "missing_observer" if missing else ("active" if active else "quiet"),
        "observers": observers,
        "alerts": active,
        "snoozed_count": sum(
            row["state"] in {"open", "expired"}
            and row["snoozed_until"] is not None
            and row["snoozed_until"] > now
            for row in rows
        ),
    }


def set_alert_state(
    path: Path, key: str, *, action: str, now: float, until: float | None = None
) -> dict[str, Any]:
    """Explicitly resolve or snooze an existing alert without deciding a source gate.

    Parameters
    ----------
    path : pathlib.Path
        Owner-local queue database.
    key : str
        Exact alert key.
    action : str
        ``resolve`` or ``snooze``.
    now : float
        Wall-clock transition timestamp.
    until : float or None
        Future wake time for ``snooze``.

    Returns
    -------
    dict[str, Any]
        Updated alert row.

    Raises
    ------
    AttentionStoreError
        If the key, action or transition is invalid.
    """
    if not math.isfinite(now) or action not in {"resolve", "snooze"} or not key or len(key) > 256:
        raise AttentionStoreError("invalid attention action or key")
    if action == "snooze" and (until is None or not math.isfinite(until) or until <= now):
        raise AttentionStoreError("snooze must end in the future")
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM alerts WHERE key=?", (key,)).fetchone()
        if row is None:
            raise AttentionStoreError("alert not found")
        if row["state"] == "resolved":
            raise AttentionStoreError("alert is already resolved")
        db.execute(
            "UPDATE alerts SET state=?, snoozed_until=?, updated_at=? WHERE key=?",
            ("resolved" if action == "resolve" else row["state"], until, now, key),
        )
        updated = db.execute("SELECT * FROM alerts WHERE key=?", (key,)).fetchone()
        if updated is None:
            raise AttentionStoreError("alert disappeared")
        return _row(updated)


def mark_notified(path: Path, keys: Sequence[str], *, now: float) -> int:
    """Record one successful bounded desktop preview per current source revision."""
    if not math.isfinite(now) or len(keys) > 50 or len(keys) != len(set(keys)):
        raise AttentionStoreError("notification batch is invalid")
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        marked = 0
        for key in keys:
            cursor = db.execute(
                "UPDATE alerts SET notified_revision=source_revision||':'||state, updated_at=? "
                "WHERE key=? AND state IN ('open','expired') "
                "AND (snoozed_until IS NULL OR snoozed_until<=?) "
                "AND (notified_revision IS NULL OR notified_revision<>source_revision||':'||state)",
                (now, key, now),
            )
            marked += cursor.rowcount
    return marked
