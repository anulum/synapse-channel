# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-local human app task queue
"""Durable, explicit handoff and verification of human app work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from synapse_channel.core.entitlement_store import (
    default_entitlement_store,
    read_events,
)
from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.entitlements import parse_quantity, parse_time
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
    assert_owner_only_file_path,
)

_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_STEPS: Final = {
    "accept": ("offered", "accepted"),
    "start": ("accepted", "running"),
    "decline": ("offered", "declined"),
    "cancel": (("offered", "accepted", "running", "result_attached"), "cancelled"),
}
_MAX_JSON: Final = 1_048_576


class AppTaskError(ValueError):
    """A task or transition is invalid or its private store is unavailable."""


def default_app_task_store() -> Path:
    """Keep app prompts and returned content outside the shared hub."""
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local" / "state"
    return root / "synapse-channel" / "app-tasks" / "queue.sqlite3"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AppTaskError("task data must be finite JSON") from exc
    if len(encoded.encode()) > _MAX_JSON:
        raise AppTaskError("task data exceeds one MiB")
    return encoded


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise AppTaskError(f"{field} must be a bounded opaque id")
    return value


def _open(path: Path) -> sqlite3.Connection:
    parent = path.parent
    try:
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True)
            apply_owner_only_dir(parent)
        assert_owner_only_dir_path(parent, purpose="app task directory")
        if path.is_symlink():
            raise AppTaskError("app task store must not be a symlink")
        existed = path.exists()
        if existed:
            assert_owner_only_file_path(path, purpose="app task store")
        db = sqlite3.connect(path, timeout=5.0)
        db.row_factory = sqlite3.Row
        if not existed:
            apply_owner_only_file(path)
        db.execute("PRAGMA foreign_keys=ON")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0 and not existed:
            db.executescript(
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "bundle TEXT NOT NULL, allowance TEXT NOT NULL, result TEXT, usage TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
                "CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "task_id TEXT NOT NULL REFERENCES tasks(task_id), action TEXT NOT NULL, "
                "at TEXT NOT NULL, detail TEXT NOT NULL);"
                "PRAGMA user_version=1;"
            )
        elif version != 1:
            raise AppTaskError(f"unsupported app task store version {version}")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AppTaskError("app task store integrity check failed")
        return db
    except AppTaskError:
        if "db" in locals():
            db.close()
        raise
    except (OSError, sqlite3.DatabaseError, SecurePathError) as exc:
        if "db" in locals():
            db.close()
        raise AppTaskError(f"cannot open private app task store: {exc}") from exc


def _allowance(window_id: str, ledger: Path, at: datetime) -> dict[str, object]:
    try:
        report = entitlement_view(read_events(ledger), as_of=at, private=True)
    except (ValueError, OSError) as exc:
        raise AppTaskError(f"allowance ledger unavailable: {exc}") from exc
    for pool in report["pools"]:
        for window in pool["windows"]:
            if window["window_id"] == window_id:
                if not pool["account_usable"] or not window["current"]:
                    raise AppTaskError("allowance window is not currently usable")
                age = (at - parse_time(window["recorded_at"], "recorded_at")).total_seconds()
                if age < 0:
                    raise AppTaskError("allowance source is dated in the future")
                return {
                    "window_id": window_id,
                    "window_event_id": window["revision"],
                    "unit": window["unit"],
                    "source": window["source"],
                    "confidence": window["confidence"],
                    "source_age_seconds": age,
                    "remaining": window["remaining"],
                    "balance_evidence": window["balance_evidence"],
                }
    raise AppTaskError("allowance window not found")


def _validate_bundle(bundle: dict[str, Any]) -> tuple[str, str]:
    if set(bundle) != {"task_id", "prompt", "input", "window_id", "expires_at", "verifier"}:
        raise AppTaskError(
            "bundle requires task_id, prompt, input, window_id, expires_at and verifier"
        )
    task_id = _id(bundle["task_id"], "task_id")
    _id(bundle["window_id"], "window_id")
    prompt = bundle["prompt"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32768:
        raise AppTaskError("prompt must be nonempty text of at most 32768 characters")
    if not isinstance(bundle["input"], dict):
        raise AppTaskError("input must be a JSON object")
    parse_time(bundle["expires_at"], "expires_at")
    verifier = bundle["verifier"]
    if (
        not isinstance(verifier, dict)
        or set(verifier) != {"field", "equals"}
        or not isinstance(verifier["field"], str)
        or _ID.fullmatch(verifier["field"]) is None
        or not isinstance(verifier["equals"], (str, int, bool))
    ):
        raise AppTaskError("verifier requires one bounded result field and scalar expected value")
    return task_id, _json(bundle)


def _task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "state": row["state"],
        "bundle": json.loads(row["bundle"]),
        "allowance": json.loads(row["allowance"]),
        "result": json.loads(row["result"]) if row["result"] else None,
        "usage": json.loads(row["usage"]) if row["usage"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _load(db: sqlite3.Connection, task_id: str, at: datetime) -> sqlite3.Row:
    row = db.execute("SELECT * FROM tasks WHERE task_id=?", (_id(task_id, "task_id"),)).fetchone()
    if row is None:
        raise AppTaskError("task not found")
    if (
        row["state"] in {"offered", "accepted"}
        and parse_time(json.loads(row["bundle"])["expires_at"], "expires_at") <= at
    ):
        stamp = at.isoformat()
        db.execute(
            "UPDATE tasks SET state='expired', updated_at=? WHERE task_id=?", (stamp, task_id)
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (task_id, "expire", stamp, "{}"),
        )
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise AppTaskError("task disappeared during expiry transition")
    return row


def offer(
    path: Path, bundle: dict[str, Any], *, ledger: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Offer a prompt with a current C04 allowance source and fixed verifier."""
    at = _now() if now is None else now
    task_id, encoded = _validate_bundle(bundle)
    stamp = at.isoformat()
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if previous is not None:
            if previous["bundle"] != encoded:
                raise AppTaskError("task id already has a different bundle")
            return _task(_load(db, task_id, at))
        if parse_time(bundle["expires_at"], "expires_at") <= at:
            raise AppTaskError("offer already expired")
        allowance = _json(
            _allowance(
                bundle["window_id"], default_entitlement_store() if ledger is None else ledger, at
            )
        )
        db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
            (task_id, "offered", encoded, allowance, None, None, stamp, stamp),
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (task_id, "offer", stamp, allowance),
        )
        return _task(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def get(path: Path, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Read one task, materialising expiry without treating it as acceptance."""
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        return _task(_load(db, task_id, _now() if now is None else now))


def history(path: Path, task_id: str) -> tuple[dict[str, Any], ...]:
    """Return durable transitions and provenance without result content."""
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _load(db, task_id, _now())
        rows = db.execute(
            "SELECT action, at, detail FROM events WHERE task_id=? ORDER BY event_id",
            (task_id,),
        )
        return tuple(
            {"action": row["action"], "at": row["at"], "detail": json.loads(row["detail"])}
            for row in rows
        )


def advance(
    path: Path, task_id: str, action: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Accept, start, decline or cancel an offer with a durable transition."""
    if action not in _STEPS:
        raise AppTaskError("unknown task transition")
    at = _now() if now is None else now
    allowed, destination = _STEPS[action]
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _load(db, task_id, at)
        if row["state"] not in ((allowed,) if isinstance(allowed, str) else allowed):
            raise AppTaskError(f"cannot {action} task in state {row['state']}")
        stamp = at.isoformat()
        db.execute(
            "UPDATE tasks SET state=?, updated_at=? WHERE task_id=?", (destination, stamp, task_id)
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (task_id, action, stamp, "{}"),
        )
        return _task(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def attach(
    path: Path,
    task_id: str,
    result: dict[str, Any],
    *,
    actor: str = "operator:cli",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind one untrusted result envelope to its task and source."""
    if (
        set(result) != {"task_id", "payload", "provenance", "usage"}
        or result.get("task_id") != task_id
    ):
        raise AppTaskError(
            "result envelope must name exactly its task, payload, provenance and usage"
        )
    if (
        not isinstance(result["payload"], dict)
        or not isinstance(result["provenance"], str)
        or not result["provenance"].strip()
    ):
        raise AppTaskError("result requires an object payload and nonempty provenance")
    usage = result["usage"]
    if (
        not isinstance(usage, dict)
        or set(usage) != {"amount", "measurement"}
        or usage["measurement"] not in {"measured", "manual"}
    ):
        raise AppTaskError("usage requires amount and measured or manual classification")
    parse_quantity(usage["amount"], "amount")
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
        raise AppTaskError("result actor must be a bounded identity")
    encoded = _json(result)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    at = _now() if now is None else now
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _load(db, task_id, at)
        if row["state"] == "result_attached" and row["result"] == encoded:
            return _task(row)
        if row["state"] != "running":
            raise AppTaskError(f"cannot attach result in state {row['state']}")
        stamp = at.isoformat()
        db.execute(
            "UPDATE tasks SET state='result_attached', result=?, usage=?, "
            "updated_at=? WHERE task_id=?",
            (encoded, _json(usage), stamp, task_id),
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (
                task_id,
                "attach",
                stamp,
                _json({"sha256": digest, "provenance": result["provenance"], "actor": actor}),
            ),
        )
        return _task(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def verify(path: Path, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Complete only when the offer's fixed result predicate passes."""
    at = _now() if now is None else now
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _load(db, task_id, at)
        if row["state"] != "result_attached":
            raise AppTaskError(f"cannot verify task in state {row['state']}")
        rule = json.loads(row["bundle"])["verifier"]
        result = json.loads(row["result"])
        actual = result["payload"].get(rule["field"])
        if type(actual) is not type(rule["equals"]) or actual != rule["equals"]:
            raise AppTaskError("task-specific result verifier did not pass")
        stamp = at.isoformat()
        db.execute(
            "UPDATE tasks SET state='verified', updated_at=? WHERE task_id=?", (stamp, task_id)
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (task_id, "verify", stamp, _json(rule)),
        )
        return _task(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def correct_usage(
    path: Path, task_id: str, amount: str, reason: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Record an explicit manual correction without rewriting the source result."""
    parse_quantity(amount, "amount")
    if not reason.strip() or len(reason) > 512:
        raise AppTaskError("usage correction requires a bounded reason")
    at = _now() if now is None else now
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _load(db, task_id, at)
        if row["state"] != "verified":
            raise AppTaskError("usage correction requires a verified task")
        prior = json.loads(row["usage"])
        revised = {
            "amount": amount,
            "measurement": "manual",
            "previous_amount": prior["amount"],
            "reason": reason,
        }
        stamp = at.isoformat()
        db.execute(
            "UPDATE tasks SET usage=?, updated_at=? WHERE task_id=?",
            (_json(revised), stamp, task_id),
        )
        db.execute(
            "INSERT INTO events(task_id, action, at, detail) VALUES(?,?,?,?)",
            (task_id, "correct_usage", stamp, _json(revised)),
        )
        return _task(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
