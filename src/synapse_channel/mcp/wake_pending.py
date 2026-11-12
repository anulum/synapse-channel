# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic historical pending work recovery
"""Remember finite historical assignment fingerprints with their pending wake bits."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from synapse_channel.core.pending_work import MAX_PENDING_WORK, PendingItem
from synapse_channel.mcp.pending_work import PendingSnapshot, decode_pending_items
from synapse_channel.mcp.wake_database import wake_database
from synapse_channel.mcp.wake_pointer import (
    MAX_WAKE_INTEGER,
    WAKE_SOURCES,
    WakeLedgerError,
    WakeScope,
)


def _stored_items(connection: sqlite3.Connection) -> tuple[PendingItem, ...]:
    """Refuse corrupt or oversized historical recovery state without resetting it."""
    row = connection.execute(
        "SELECT position FROM sources WHERE source='pending-legacy'"
    ).fetchone()
    try:
        if row is not None and (type(row[0]) is not str or len(row[0]) > 131072):
            raise ValueError("invalid pending recovery record")
        return decode_pending_items(json.loads(row[0])) if row else ()
    except (ValueError, TypeError, RecursionError):
        raise WakeLedgerError("invalid stored pending recovery record") from None


class WakePendingObservation:
    """Recover historical pending work once without consuming native work receipts."""

    path: Path
    scope: WakeScope

    def observe_pending_work(self, snapshot: PendingSnapshot) -> bool:
        """Commit new historical fingerprints and all matching wake bits atomically.

        Parameters
        ----------
        snapshot : PendingSnapshot
            Complete bounded snapshot for this exact native seat and hub.
            Current producers are excluded by the native index and recovered
            fingerprints remain remembered after task completion or restart.

        Returns
        -------
        bool
            Whether this snapshot added unseen historical pending work. A native
            processing ACK or a remote notification receipt is never created.
        """
        try:
            if (
                snapshot.identity != self.scope.seat
                or snapshot.hub_id != self.scope.hub_id
                or type(snapshot.items) is not tuple
            ):
                raise ValueError("invalid pending snapshot scope")
            incoming = decode_pending_items([x.encode() for x in snapshot.items])
        except ValueError:
            raise WakeLedgerError("invalid historical pending snapshot") from None
        with wake_database(self.path, self.scope) as connection:
            known = _stored_items(connection)
            retained = {x.key: x for x in known}
            bits = 0
            for item in incoming:
                previous = retained.get(item.key)
                if previous is not None and previous != item:
                    raise WakeLedgerError("pending assignment family changed")
                if previous is None:
                    retained[item.key] = item
                    bits |= 1 << WAKE_SOURCES.index(item.kind)
            if len(retained) > MAX_PENDING_WORK:
                raise WakeLedgerError("pending recovery capacity exhausted")
            if not bits:
                return False
            values = [x.encode() for x in sorted(retained.values(), key=lambda x: (x.kind, x.key))]
            connection.execute(
                "INSERT INTO sources VALUES ('pending-legacy',?) ON CONFLICT(source) "
                "DO UPDATE SET position=excluded.position",
                (json.dumps(values),),
            )
            if (
                connection.execute(
                    "UPDATE meta SET revision=revision+1,pending=pending|? WHERE revision<?",
                    (bits, MAX_WAKE_INTEGER),
                ).rowcount
                != 1
            ):
                raise WakeLedgerError("wake revision budget exhausted")
            return True
