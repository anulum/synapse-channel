# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — validated notification attempt and operator receipts
"""Validate durable attempt fences before dispatch or metadata-only inspection."""

from __future__ import annotations

import sqlite3
from typing import Literal, TypedDict

from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WAKE_MAX_ATTEMPTS,
    WakeAttempt,
    WakeLedgerError,
    WakeScope,
)

ControlOutcome = Literal["queued", "retired", "already_applied", "changed", "busy", "unknown"]


class WakeReceipt(TypedDict):
    """Expose bounded transport and operator metadata for one retained hint."""

    id: str
    revision: int
    state: str
    attempt: int
    cycle_attempt: int
    http_status: int
    control_action: str
    control_at: int


class WakeInspection(TypedDict):
    """Bind aggregate counters and retained receipts to one storage snapshot."""

    summary: dict[str, int]
    deliveries: list[WakeReceipt]


def _delivery_fence(row: sqlite3.Row) -> None:
    """Require a monotonic lifetime counter and an independently bounded retry cycle."""
    if (
        type(row["attempt"]) is not int
        or not 1 <= row["attempt"] <= MAX_WAKE_INTEGER
        or type(row["cycle_base"]) is not int
        or not 0 <= row["cycle_base"] <= row["attempt"]
        or not 0 <= row["attempt"] - row["cycle_base"] <= WAKE_MAX_ATTEMPTS
        or (
            row["attempt"] == row["cycle_base"]
            and (row["state"] != "retry" or row["control_action"] != "redrive")
        )
    ):
        raise WakeLedgerError("invalid stored wake attempt")


def _transport_receipt(row: sqlite3.Row) -> None:
    """Validate bounded transport and operator metadata before exposing any fields."""
    if (
        type(row["deadline"]) is not int
        or not 0 <= row["deadline"] <= MAX_WAKE_INTEGER
        or type(row["due"]) is not int
        or not 0 <= row["due"] <= MAX_WAKE_INTEGER
        or row["control_action"] not in ("", "redrive", "retire")
        or type(row["control_at"]) is not int
        or not 0 <= row["control_at"] <= MAX_WAKE_INTEGER
        or type(row["status"]) is not int
        or not (row["status"] == 0 or 100 <= row["status"] <= 599)
    ):
        raise WakeLedgerError("invalid stored wake attempt")


def wake_attempt(row: sqlite3.Row, scope: WakeScope) -> WakeAttempt:
    """Require an immutable body and lifetime counter independent of retry cycles."""
    _delivery_fence(row)
    _transport_receipt(row)
    if (
        type(row["id"]) is not str
        or type(row["revision"]) is not int
        or type(row["bits"]) is not int
        or type(row["body"]) is not bytes
        or row["body"] != scope.body(row["id"], row["revision"], row["bits"])
    ):
        raise WakeLedgerError("invalid stored wake attempt")
    return WakeAttempt(row["id"], row["revision"], row["body"], row["attempt"], row["deadline"])


def delivery_receipts(connection: sqlite3.Connection, scope: WakeScope) -> list[WakeReceipt]:
    """Return retained transport metadata without bodies, keys or endpoint URLs."""
    receipts: list[WakeReceipt] = []
    for row in connection.execute("SELECT * FROM deliveries ORDER BY ordinal"):
        attempt = wake_attempt(row, scope)
        receipts.append(
            {
                "id": attempt.delivery_id,
                "revision": attempt.revision,
                "state": row["state"],
                "attempt": attempt.attempt,
                "cycle_attempt": attempt.attempt - row["cycle_base"],
                "http_status": row["status"],
                "control_action": row["control_action"],
                "control_at": row["control_at"],
            }
        )
    return receipts


def delivery_summary(connection: sqlite3.Connection, scope: WakeScope) -> dict[str, int]:
    """Read counters and the current transport deadline within the caller's snapshot."""
    meta = connection.execute("SELECT * FROM meta").fetchone()
    active = connection.execute(
        "SELECT * FROM deliveries WHERE state IN ('inflight','retry')"
    ).fetchone()
    next_attempt_at = 0
    if active is not None:
        wake_attempt(active, scope)
        next_attempt_at = active["deadline"] if active["state"] == "inflight" else active["due"]
    return {
        "next_attempt_at": next_attempt_at,
        "revision": meta["revision"],
        "pending_sources": meta["pending"],
        "transport_accepted": meta["accepted"],
        "dead_lettered": meta["failed"],
        "retired_receipts": meta["retired"],
        "retained_receipts": connection.execute("SELECT count(*) FROM deliveries").fetchone()[0],
        "unresolved_failures": connection.execute(
            "SELECT count(*) FROM deliveries WHERE state='dead-letter'"
        ).fetchone()[0],
    }
