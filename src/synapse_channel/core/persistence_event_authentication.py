# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""EventAuthentication implementation for the existing EventStore facade."""

from __future__ import annotations

import logging
from functools import cache
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.event_row_recovery import (
    CORRUPT_EVENT_KIND,
    CorruptEventRow,
    decode_event_row,
    unauthenticated_row,
)
from synapse_channel.core.persistence_connection import StoreConnection
from synapse_channel.core.persistence_schema import create_row_mac_index

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        StoredEvent,
    )
logger = logging.getLogger("synapse_channel.core.persistence")


@cache
def _record_module() -> ModuleType:
    """Load the public record module once, retaining late constructor lookup.

    Delaying the module lookup until decoding also permits either family or
    facade to be imported first, without accessing a partially defined class.
    """
    return import_module("synapse_channel.core.persistence")


class EventAuthentication:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def ensure_mac_column(self) -> None:
        """Add the row-authentication column to a log created before it existed."""
        columns = {
            str(info[1]) for info in self.storage.connection.execute("PRAGMA table_info(events)")
        }
        if "mac" not in columns:
            self.storage.connection.execute("ALTER TABLE events ADD COLUMN mac TEXT")
        create_row_mac_index(self.storage.connection)

    def insert_event(self, ts: float, kind: str, payload: str) -> int:
        """Insert one event row in the caller's transaction and return its sequence.

        Every writer of the ``events`` table goes through here, so with a row key
        enabled each row carries its MAC from the moment it exists (K4-REPLAY).
        """
        cursor = self.storage.connection.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (ts, kind, payload)
        )
        sequence = int(cursor.lastrowid or 0)
        if self.storage.row_mac is not None:
            self.storage.connection.execute(
                "UPDATE events SET mac = ? WHERE seq = ?",
                (self.storage.row_mac.mac(sequence, ts, kind, payload), sequence),
            )
        return sequence

    def has_row_macs(self) -> bool:
        """Return whether any stored row carries a row MAC."""
        with self.storage.lock:
            row = self.storage.connection.execute(
                "SELECT 1 FROM events WHERE mac IS NOT NULL LIMIT 1"
            ).fetchone()
        return row is not None

    def verify_recovery_authentication(self) -> None:
        """Refuse recovery without the original row key or with unauthenticated rows.

        The delivery recovery transaction holds this store's lock and the SQLite
        write reservation. Encryption alone does not configure the row-MAC writer.
        Rechecking all rows here prevents an unauthenticated binding event or an
        operator recovery over quarantined history.
        """
        from synapse_channel.core.delivery_modes import DeliveryRefusal

        if self.storage.row_mac is None:
            if self.has_row_macs():
                raise DeliveryRefusal(
                    "row_authentication_required",
                    "delivery recovery requires the original event-row authentication key",
                )
            return
        if self.enable_row_mac(self.storage.row_mac):
            raise DeliveryRefusal(
                "journal_recovery_required", "delivery recovery refuses unauthenticated event rows"
            )

    def enable_row_mac(self, key: RowMacKey) -> tuple[CorruptEventRow, ...]:
        """Authenticate rows written from now on and quarantine unauthenticated ones.

        Every row after ``key.since_seq`` must carry ``key``'s MAC. A row that does
        not is recorded as quarantined: reads return it as a ``corrupt_event``
        marker, so replay skips it and the hub's journal-recovery gate refuses
        mutations until an operator resolves it.

        Parameters
        ----------
        key : RowMacKey
            The hub's row key.

        Returns
        -------
        tuple[CorruptEventRow, ...]
            The quarantined rows, by sequence.
        """
        quarantine: dict[int, CorruptEventRow] = {}
        with self.storage.lock:
            rows = self.storage.connection.execute(
                "SELECT seq, ts, kind, payload, mac FROM events WHERE seq > ? ORDER BY seq",
                (key.since_seq,),
            )
            for seq, ts, kind, payload, mac in rows:
                if not key.verify(int(seq), ts, kind, payload, mac):
                    quarantine[int(seq)] = unauthenticated_row(
                        int(seq), kind, payload, mac_present=mac is not None
                    )
            self.storage.row_mac = key
            self.storage.row_quarantine = quarantine
        return tuple(quarantine.values())

    @property
    def row_quarantine(self) -> frozenset[int]:
        """Sequences quarantined because their row MAC failed."""
        return frozenset(self.storage.row_quarantine)

    def decode_event(
        self, row: tuple[object, object, object, object], *, apply_row_quarantine: bool = True
    ) -> StoredEvent:
        """Return one validated event or a non-secret corrupt-row marker.

        A row quarantined by :meth:`enable_row_mac` is returned as its marker unless
        ``apply_row_quarantine`` is false (the Merkle commitment folds raw rows).
        """
        event_type: type[StoredEvent] = _record_module().StoredEvent
        seq = row[0]
        marker = (
            self.storage.row_quarantine.get(seq)
            if apply_row_quarantine and isinstance(seq, int)
            else None
        )
        if marker is not None:
            return event_type(
                seq=marker.seq, ts=0.0, kind=CORRUPT_EVENT_KIND, payload=marker.as_payload()
            )
        decoded = decode_event_row(row)
        return event_type(
            seq=decoded.seq, ts=decoded.ts, kind=decoded.kind, payload=decoded.payload
        )
