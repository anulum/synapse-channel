# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""EventMaintenance implementation for the existing EventStore facade."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from synapse_channel.core.persistence_connection import StoreConnection

if TYPE_CHECKING:
    pass
logger = logging.getLogger("synapse_channel.core.persistence")


class EventMaintenance:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def delete(self, seqs: Iterable[int]) -> int:
        """Delete the events with these sequence numbers; return how many were removed.

        A maintenance primitive for retention/compaction
        (:mod:`synapse_channel.core.compaction`). A deleted sequence is never
        reused — the ``AUTOINCREMENT`` primary key only ever increases — so a
        downstream :meth:`read_since` cursor stays correct across a compaction: a
        removed sequence simply becomes a gap the cursor walks past. The delete
        commits at ``NORMAL`` durability; a delete lost to an OS crash is harmless
        because re-running compaction removes the same rows again.

        Parameters
        ----------
        seqs : Iterable[int]
            Sequence numbers to remove; an empty iterable is a no-op.

        Returns
        -------
        int
            The number of rows actually deleted.
        """
        seq_list = [int(s) for s in seqs]
        if not seq_list:
            return 0
        sql = (
            "DELETE FROM events WHERE seq = ? AND kind <> 'protected_write' AND "
            "substr(kind, 1, 16) <> 'protected_write_' AND NOT (kind IN ('claim', "
            "'task_update', 'checkpoint', 'handoff', 'release') AND EXISTS (SELECT "
            "1 FROM events AS protected_history WHERE protected_history.kind = "
            "'protected_write' OR protected_history.kind GLOB "
            "'protected_write_*'))"
        )
        if self.storage.has_aef_outbox:
            sql += (
                " AND NOT EXISTS (SELECT 1 FROM aef_outbox WHERE legacy_seq = "
                "events.seq AND receipt_id IS "
                "NULL)"
            )
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM operations WHERE events.seq BETWEEN "
            "operations.first_event_seq AND "
            "operations.commit_seq)"
        )
        with self.storage.lock:
            cursor = self.storage.connection.executemany(sql, ((seq,) for seq in seq_list))
            self.storage.connection.commit()
        return int(cursor.rowcount)

    def vacuum(self) -> None:
        """Reclaim free pages left by deletes, shrinking the database file on disk.

        A ``DELETE`` marks pages free for reuse but does not return them to the
        filesystem, so a large retention sweep leaves the file the same size until
        ``VACUUM`` rewrites the database to release the free pages. It rewrites the
        whole database, so call it from a maintenance path, not the hot loop.
        """
        with self.storage.lock:
            self.storage.connection.commit()
            self.storage.connection.execute("VACUUM")
