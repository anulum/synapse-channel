# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""EventWriter implementation for the existing EventStore facade."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.persistence_connection import StoreConnection

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        StoredEvent,
    )
logger = logging.getLogger("synapse_channel.core.persistence")


class EventWriter:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def append_batch(
        self,
        events: Iterable[tuple[str, Mapping[str, Any]]],
        *,
        ts: float | None = None,
        durable: bool = False,
    ) -> tuple[int, ...]:
        """Append several events in one SQLite transaction.

        The whole batch commits or rolls back as one unit. All rows share one
        timestamp so adjacent state and provenance events describe the same
        authoritative transition.

        Parameters
        ----------
        events : iterable[tuple[str, Mapping[str, Any]]]
            Ordered ``(kind, payload)`` pairs.
        ts : float or None, optional
            Shared timestamp; the system clock is used when omitted.
        durable : bool, optional
            Use ``synchronous=FULL`` for the batch commit.

        Returns
        -------
        tuple[int, ...]
            Assigned sequence numbers in input order. An empty input returns
            an empty tuple without opening a transaction.
        """
        stamp = time.time() if ts is None else float(ts)
        rows = [
            (stamp, str(kind), json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":")))
            for kind, payload in events
        ]
        if not rows:
            return ()
        with self.storage.lock:
            if durable:
                self.storage.connection.execute("PRAGMA synchronous=FULL")
            try:
                sequences = []
                for row in rows:
                    sequence = self.storage.insert_event(*row)
                    sequences.append(sequence)
                    if row[1] in self.storage.aef_kinds:
                        self.storage.connection.execute(
                            "INSERT INTO aef_outbox (legacy_seq, receipt_id) VALUES (?, NULL)",
                            (sequence,),
                        )
                self.storage.connection.commit()
            except BaseException:
                self.storage.connection.rollback()
                raise
            finally:
                if durable:
                    try:
                        self.storage.connection.execute("PRAGMA synchronous=NORMAL")
                    except BaseException:
                        logger.exception("Could not restore SQLite synchronous=NORMAL")
        return tuple(sequences)

    def pending_aef_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        """Return queued legacy rows awaiting native AEF reconciliation."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or (limit > 10000):
            raise ValueError("AEF outbox limit must be an integer from 1 through 10000")
        if not self.storage.has_aef_outbox:
            return ()
        with self.storage.lock:
            rows = self.storage.connection.execute(
                (
                    "SELECT e.seq, e.ts, e.kind, e.payload FROM aef_outbox AS o JOIN "
                    "events AS e ON e.seq = o.legacy_seq WHERE o.receipt_id IS NULL ORDER "
                    "BY e.seq LIMIT "
                    "?"
                ),
                (limit,),
            ).fetchall()
        return tuple(self.storage.decode_event(row, True) for row in rows)

    def mark_aef_delivered(self, legacy_seq: int, receipt_id: str) -> None:
        """Durably bind one queued legacy row to its emitted AEF receipt."""
        if isinstance(legacy_seq, bool) or not isinstance(legacy_seq, int) or legacy_seq < 1:
            raise ValueError("AEF outbox sequence must be a positive integer")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError("AEF outbox receipt id must be non-empty text")
        with self.storage.lock:
            self.storage.connection.execute("PRAGMA synchronous=FULL")
            try:
                row = self.storage.connection.execute(
                    "SELECT receipt_id FROM aef_outbox WHERE legacy_seq = ?", (legacy_seq,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"legacy sequence {legacy_seq} is not queued for AEF")
                current = row[0]
                if current is not None and str(current) != receipt_id:
                    raise ValueError("AEF outbox sequence is already bound to another receipt")
                self.storage.connection.execute(
                    "UPDATE aef_outbox SET receipt_id = ? WHERE legacy_seq = ?",
                    (receipt_id, legacy_seq),
                )
                self.storage.connection.commit()
            except BaseException:
                self.storage.connection.rollback()
                raise
            finally:
                self.storage.connection.execute("PRAGMA synchronous=NORMAL")

    def aef_delivery(self, legacy_seq: int) -> str | None:
        """Return the delivered receipt id, or ``None`` for pending/absent rows."""
        if not self.storage.has_aef_outbox:
            return None
        with self.storage.lock:
            row = self.storage.connection.execute(
                "SELECT receipt_id FROM aef_outbox WHERE legacy_seq = ?", (legacy_seq,)
            ).fetchone()
        return None if row is None or row[0] is None else str(row[0])
