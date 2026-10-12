# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""One owner connection, serialised lock and explicitly shared event state."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.event_row_recovery import CorruptEventRow

if TYPE_CHECKING:
    from synapse_channel.core.persistence import DeliveryReceiptState, StoredEvent
    from synapse_channel.core.persistence_event_authentication import EventAuthentication
    from synapse_channel.core.persistence_event_maintenance import EventMaintenance
    from synapse_channel.core.persistence_event_reads import EventReader
    from synapse_channel.core.persistence_events import EventWriter
    from synapse_channel.core.persistence_operations import OperationTable
    from synapse_channel.core.persistence_receipts import ReceiptTable
logger = logging.getLogger("synapse_channel.core.persistence")


@dataclass
class StoreConnection:
    """Share the owner's DB-API connection and deferred event codecs.

    The DB-API connection remains dynamic to support SQLite and SQLCipher.
    Callback lookup happens at use time through the original owner seams.
    """

    connection: Any
    lock: threading.RLock
    insert_event: Callable[[float, str, str], int]
    decode_event: Callable[[tuple[object, object, object, object], bool], StoredEvent]
    insert_receipt: Callable[
        [tuple[str, int, str, dict[str, Any], dict[str, Any], DeliveryReceiptState], float], None
    ]
    aef_kinds: frozenset[str] = frozenset()
    has_aef_outbox: bool = False
    row_mac: RowMacKey | None = None
    row_quarantine: dict[int, CorruptEventRow] = field(default_factory=dict)

    def restore_normal(self, committed: bool) -> None:
        """Restore NORMAL without contradicting an already committed outcome."""
        try:
            self.connection.execute("PRAGMA synchronous=NORMAL")
        except BaseException:
            if not committed:
                raise
            logger.exception("Could not restore SQLite synchronous=NORMAL")


@dataclass(frozen=True)
class StorageFamilies:
    """Cohesive table-family collaborators sharing one transaction owner."""

    events: EventWriter
    reads: EventReader
    authentication: EventAuthentication
    maintenance: EventMaintenance
    receipts: ReceiptTable
    operations: OperationTable


def create_families(storage: StoreConnection) -> StorageFamilies:
    """Bind table responsibilities without opening, migrating or committing."""
    from synapse_channel.core.persistence_event_authentication import EventAuthentication
    from synapse_channel.core.persistence_event_maintenance import EventMaintenance
    from synapse_channel.core.persistence_event_reads import EventReader
    from synapse_channel.core.persistence_events import EventWriter
    from synapse_channel.core.persistence_operations import OperationTable
    from synapse_channel.core.persistence_receipts import ReceiptTable

    return StorageFamilies(
        EventWriter(storage),
        EventReader(storage),
        EventAuthentication(storage),
        EventMaintenance(storage),
        ReceiptTable(storage),
        OperationTable(storage),
    )


def restrict_file(path: str) -> None:
    """Restrict ``path`` to owner-only access (``0o600``).

    Parameters
    ----------
    path : str
        Filesystem path to chmod. The ``:memory:`` database has no on-disk
        file, and a sidecar may not exist yet; both cases are skipped silently,
        as is any platform that does not support ``chmod``.
    """
    if path.startswith(":memory:"):
        return
    with contextlib.suppress(OSError):
        os.chmod(path, 384)
