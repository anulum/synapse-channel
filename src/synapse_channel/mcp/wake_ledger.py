# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — coalesced durable remote wake outbox
"""Persist wake hints independently of native inbox and business receipts."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Literal

from synapse_channel.mcp.wake_database import MAX_WAKE_RECEIPTS, wake_database
from synapse_channel.mcp.wake_observation import WakeObservation
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WakeAttempt,
    WakeLedgerError,
    WakeScope,
)
from synapse_channel.mcp.wake_pointer import (
    WAKE_LEASE_SECONDS as WAKE_LEASE_SECONDS,
)
from synapse_channel.mcp.wake_pointer import (
    WAKE_MAX_ATTEMPTS as WAKE_MAX_ATTEMPTS,
)
from synapse_channel.mcp.wake_pointer import (
    WAKE_RETRY_SECONDS as WAKE_RETRY_SECONDS,
)
from synapse_channel.mcp.wake_receipts import (
    ControlOutcome,
    WakeInspection,
    WakeReceipt,
    delivery_receipts,
    delivery_summary,
    wake_attempt,
)

WakeOutcome = Literal["accepted", "retry", "rejected"]


def _time(now: int) -> None:
    """Keep durable timestamps finite and reject boolean/coercion inputs."""
    if type(now) is not int or not 0 <= now <= MAX_WAKE_INTEGER - 60:
        raise WakeLedgerError("invalid wake timestamp")


class WakeLedger(WakeObservation):
    """Own one active delivery, coalesced newer changes and bounded receipt history.

    Parameters
    ----------
    root : Path
        Dedicated owner-private state directory.
    scope : WakeScope
        Provisioned subject/project/seat/key-configuration digest and actual hub.

    Notes
    -----
    Sources retain only scanned positions, source bits or projection digests. A delivery's
    ID and bytes never change after its first attempt, including crash/retry.
    Newer changes coalesce separately. An accepted HTTP attempt never consumes
    a native inbox cursor or proves model processing. Four attempts per cycle exhaust
    into a dead letter; subsequent genuine changes can create a newer hint.
    Only accepted or operator-retired receipts may age out, with a retirement counter.
    """

    def __init__(self, root: Path, scope: WakeScope) -> None:
        """Bind the durable file without opening any native or HTTP connection."""
        super().__init__(root / (scope.key + ".sqlite3"), scope)

    def acquire(self, now: int) -> WakeAttempt | None:
        """Durably fence one finite attempt before any network dispatch."""
        _time(now)
        with wake_database(self.path, self.scope) as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE state IN ('inflight','retry')"
            ).fetchone()
            if row is not None:
                wake_attempt(row, self.scope)
                if row["deadline"] > now or row["due"] > now:
                    return None
                if row["attempt"] - row["cycle_base"] >= WAKE_MAX_ATTEMPTS:
                    connection.execute(
                        "UPDATE deliveries SET state='dead-letter' WHERE id=?", (row["id"],)
                    )
                    connection.execute("UPDATE meta SET failed=failed+1")
                    return None
                if row["attempt"] == MAX_WAKE_INTEGER:
                    raise WakeLedgerError("wake attempt counter exhausted")
                connection.execute(
                    "UPDATE deliveries SET state='inflight',attempt=attempt+1,"
                    "deadline=?,due=0 WHERE id=?",
                    (now + WAKE_LEASE_SECONDS, row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM deliveries WHERE id=?", (row["id"],)
                ).fetchone()
            else:
                meta = connection.execute("SELECT * FROM meta").fetchone()
                if not meta["pending"]:
                    return None
                count = connection.execute("SELECT count(*) FROM deliveries").fetchone()[0]
                if count == MAX_WAKE_RECEIPTS:
                    removed = connection.execute(
                        "DELETE FROM deliveries WHERE ordinal="
                        "(SELECT min(ordinal) FROM deliveries "
                        "WHERE state IN ('accepted','retired'))"
                    )
                    if removed.rowcount != 1:
                        raise WakeLedgerError("wake receipt capacity reached")
                    connection.execute("UPDATE meta SET retired=retired+1")
                delivery_id = "msg_" + uuid.uuid4().hex
                body = self.scope.body(delivery_id, meta["revision"], meta["pending"])
                connection.execute(
                    "INSERT INTO deliveries(id,revision,bits,body,state,attempt,deadline,due) "
                    "VALUES (?,?,?,?,'inflight',1,?,0)",
                    (
                        delivery_id,
                        meta["revision"],
                        meta["pending"],
                        body,
                        now + WAKE_LEASE_SECONDS,
                    ),
                )
                connection.execute("UPDATE meta SET pending=0")
                row = connection.execute(
                    "SELECT * FROM deliveries WHERE id=?", (delivery_id,)
                ).fetchone()
            return wake_attempt(row, self.scope)

    def finish(
        self,
        delivery: WakeAttempt,
        now: int,
        outcome: WakeOutcome,
        *,
        http_status: int = 0,
    ) -> bool:
        """Record only the exact active attempt; stale completions cannot overwrite it."""
        _time(now)
        if (
            outcome not in ("accepted", "retry", "rejected")
            or type(http_status) is not int
            or not (http_status == 0 or 100 <= http_status <= 599)
            or (outcome == "accepted" and not 200 <= http_status < 300)
        ):
            raise WakeLedgerError("invalid wake attempt outcome")
        with wake_database(self.path, self.scope) as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE id=?", (delivery.delivery_id,)
            ).fetchone()
            if (
                row is None
                or row["state"] != "inflight"
                or wake_attempt(row, self.scope) != delivery
            ):
                return False
            cycle_attempt = delivery.attempt - row["cycle_base"]
            if outcome == "retry" and cycle_attempt < WAKE_MAX_ATTEMPTS:
                state, due = "retry", now + WAKE_RETRY_SECONDS[cycle_attempt - 1]
            else:
                state, due = ("accepted" if outcome == "accepted" else "dead-letter"), 0
                statement = (
                    "UPDATE meta SET accepted=accepted+1"
                    if state == "accepted"
                    else "UPDATE meta SET failed=failed+1"
                )
                connection.execute(statement)
            connection.execute(
                "UPDATE deliveries SET state=?,due=?,deadline=0,status=? WHERE id=?",
                (state, due, http_status, delivery.delivery_id),
            )
            return True

    def receipts(self) -> list[WakeReceipt]:
        """Inspect retained transport and operator receipts without delivery bodies."""
        with wake_database(self.path, self.scope) as connection:
            return delivery_receipts(connection, self.scope)

    def inspect(self) -> WakeInspection:
        """Read aggregate counters and receipt metadata in one storage transaction."""
        with wake_database(self.path, self.scope) as connection:
            return {
                "summary": delivery_summary(connection, self.scope),
                "deliveries": delivery_receipts(connection, self.scope),
            }

    def control_delivery(
        self,
        action: Literal["redrive", "retire"],
        delivery_id: str,
        failed_attempt: int,
        now: int,
    ) -> ControlOutcome:
        """Apply an exact operator decision to a retained failed notification.

        Parameters
        ----------
        action : {"redrive", "retire"}
            Queue one new four-attempt cycle, or permit explicit receipt retirement.
        delivery_id : str
            Exact retained notification identifier from an operator receipt.
        failed_attempt : int
            Observed lifetime attempt fence; stale decisions cannot affect a later failure.
        now : int
            Operator UTC epoch seconds, independent of any native business receipt.

        Returns
        -------
        str
            Applied, repeated, changed, unknown or blocked-by-active-delivery outcome.
            Redrive preserves ID/body and never resets the lifetime attempt fence.
            Retiring a hint neither acknowledges nor deletes native work.
        """
        _time(now)
        if (
            action not in ("redrive", "retire")
            or type(delivery_id) is not str
            or re.fullmatch(r"msg_[0-9a-f]{32}", delivery_id) is None
            or type(failed_attempt) is not int
            or not 1 <= failed_attempt <= MAX_WAKE_INTEGER
        ):
            raise WakeLedgerError("invalid wake control request")
        with wake_database(self.path, self.scope) as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if row is None:
                return "unknown"
            wake_attempt(row, self.scope)
            if row["control_action"] == action and (
                row["cycle_base"] == failed_attempt
                if action == "redrive"
                else row["state"] == "retired" and row["attempt"] == failed_attempt
            ):
                return "already_applied"
            if row["state"] != "dead-letter" or row["attempt"] != failed_attempt:
                return "changed"
            if action == "redrive":
                if row["attempt"] == MAX_WAKE_INTEGER:
                    raise WakeLedgerError("wake attempt counter exhausted")
                if connection.execute(
                    "SELECT 1 FROM deliveries WHERE state IN ('inflight','retry')"
                ).fetchone():
                    return "busy"
                connection.execute(
                    "UPDATE deliveries SET state='retry',cycle_base=attempt,deadline=0,due=?,"
                    "control_action='redrive',control_at=? WHERE id=?",
                    (now, now, delivery_id),
                )
                return "queued"
            connection.execute(
                "UPDATE deliveries SET state='retired',control_action='retire',"
                "control_at=? WHERE id=?",
                (now, delivery_id),
            )
            return "retired"

    def summary(self) -> dict[str, int]:
        """Expose bounded resource and transport counts without bodies or credentials."""
        with wake_database(self.path, self.scope) as connection:
            return delivery_summary(connection, self.scope)
