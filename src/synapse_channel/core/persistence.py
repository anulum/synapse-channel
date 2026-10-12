# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""Durable append-only event store backing the hub's authoritative state.

The hub keeps its working state in memory; this module gives that state a
crash-durable spine without adding a runtime dependency — it uses the standard
library :mod:`sqlite3` in write-ahead-log (WAL) mode. Every authoritative
mutation is appended as one event, and the state is rebuilt by replaying the log
on start-up (see :mod:`synapse_channel.core.journal`).

Durability is split honestly to match the workload. The connection runs at
``synchronous=NORMAL``, which is durable against a **process/application crash**
but may lose the most recent commit on an **OS crash or power loss**. A write
marked ``durable=True`` — the lease/claim path — is committed at
``synchronous=FULL`` so it survives an OS crash too; the high-volume chat/history
path stays at ``NORMAL``. This module never claims more than it delivers.

Every failed append is rolled back before the connection is reused. A durable
attempt also restores ``synchronous=NORMAL`` before its database exception
propagates, so one rejected write cannot leave later high-volume traffic at the
``FULL`` setting.

The connection permits worker-thread use and every operation is serialized by
one per-store reentrant lock.  Async hub paths may therefore move a complete
append off the event loop without allowing concurrent reads, maintenance, or a
second writer to use the DB-API connection at the same time.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NamedTuple

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.delivery_persistence import DeliveryPersistence
from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.event_row_recovery import (
    CorruptEventRow,
)
from synapse_channel.core.message_forward_ledger import MessageForwardLedger
from synapse_channel.core.persistence_api import (
    APPEND_DOCUMENTATION,
    READ_ALL_DOCUMENTATION,
    inherit_documentation,
)
from synapse_channel.core.persistence_connection import (
    StoreConnection,
    create_families,
    restrict_file,
)
from synapse_channel.core.persistence_event_authentication import EventAuthentication
from synapse_channel.core.persistence_event_maintenance import EventMaintenance
from synapse_channel.core.persistence_event_reads import EventReader
from synapse_channel.core.persistence_events import EventWriter
from synapse_channel.core.persistence_operations import OperationTable, decode_operation
from synapse_channel.core.persistence_receipt_codec import encode_object, prepare_transition
from synapse_channel.core.persistence_receipts import ReceiptTable
from synapse_channel.core.persistence_schema import (
    create_aef_schema,
    create_event_schema,
    create_operation_schema,
    create_receipt_schema,
)

BUSY_TIMEOUT_MS = 5000
logger = logging.getLogger(__name__)


class StoredEvent(NamedTuple):
    """One persisted event read back from the log.

    Attributes
    ----------
    seq : int
        Monotonic primary-key sequence number assigned on insert.
    ts : float
        Wall-clock time, in seconds, when the event was appended.
    kind : str
        Event kind tag (see :class:`synapse_channel.core.journal.EventKind`).
    payload : dict[str, Any]
        The decoded JSON body of the event.
    """

    seq: int
    ts: float
    kind: str
    payload: dict[str, Any]


class StoredOperation(NamedTuple):
    """One durable keyed operation and its exact replay response."""

    operation_key: str
    request_digest: str
    response: dict[str, Any]
    response_sha256: str
    first_event_seq: int
    commit_seq: int
    committed_at: float


class OperationCommitResult(NamedTuple):
    """Final outcome of an atomic operation commit attempt."""

    outcome: Literal["inserted", "replayed", "conflict"]
    operation: StoredOperation


class PendingOperationIntent(NamedTuple):
    """One committed operation intent awaiting local evidence projection."""

    operation_key: str
    intent: dict[str, Any]


class DeliveryReceiptAggregate(NamedTuple):
    """Current durable state for one receipt-requested directed chat."""

    message_seq: int
    sender: str
    target: str
    message_id: int
    client_msg_id: str
    state: str
    delivered: bool | None
    deferred: bool
    acked_by: str
    updated_event_seq: int


class PendingDeliveryNotification(NamedTuple):
    """One stable receipt frame awaiting at-least-once sender delivery."""

    notification_id: str
    message_seq: int
    phase: str
    sender: str
    frame: dict[str, Any]
    attempts: int


DeliveryReceiptState = Literal["immediate_delivered", "pending", "deferred", "expired"]


class DeliveryReceiptTransition(NamedTuple):
    """One aggregate transition paired with its sender-notification frame."""

    kind: str
    payload: Mapping[str, Any]
    notification: Mapping[str, Any]
    state: DeliveryReceiptState


class EventStore:
    """Append-only SQLite event log in WAL mode.

    Failed initialization closes the acquired connection before propagating the
    original exception, even when the caller retains its traceback. Existing
    rows are not repaired or discarded; schema DDL may already be committed.

    Parameters
    ----------
    path : str or pathlib.Path
        Database file path. ``":memory:"`` is accepted for ephemeral use, but
        only a file path survives a restart.
    key_file : str or pathlib.Path or None, optional
        Owner-only 32-byte key file. When set, the store opens through SQLCipher
        (``pip install synapse-channel[sqlcipher]``) so every page is encrypted
        at rest. Omit for the default plaintext :mod:`sqlite3` path.
    key : bytes or None, optional
        Raw 32-byte key material (tests and programmatic callers). When set,
        takes precedence over ``key_file``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        key_file: str | Path | None = None,
        key: bytes | None = None,
        aef_outbox_kinds: Iterable[str] = (),
    ) -> None:
        """Open and validate the store, retaining connection ownership on success."""
        self.path = str(path)
        self._lock = threading.RLock()
        from synapse_channel.core.persistence_sqlcipher import connect_event_store

        connection, self._encrypted = connect_event_store(
            self.path, key=key, key_file=key_file, check_same_thread=False
        )
        try:
            self.storage = StoreConnection(
                connection,
                self._lock,
                lambda ts, kind, payload: self._insert_event_row(ts, kind, payload),
                lambda row, quarantine: self._stored_event(row, apply_row_quarantine=quarantine),
                lambda transition, stamp: self._insert_delivery_receipt_transition(
                    transition, stamp=stamp
                ),
            )
            self._families = create_families(self.storage)
            self._restrict(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            create_event_schema(self._conn)
            create_operation_schema(self._conn)
            create_receipt_schema(self._conn)
            self._backfill_delivery_receipt_aggregates()
            self.storage.aef_kinds = frozenset(str(kind) for kind in aef_outbox_kinds)
            if self.storage.aef_kinds:
                create_aef_schema(self._conn)
            self.storage.has_aef_outbox = (
                self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'aef_outbox'"
                ).fetchone()
                is not None
            )
            self._ensure_mac_column()
            self.delivery = DeliveryPersistence(
                self._conn,
                self._lock,
                insert_event=self._insert_event_row,
                verify_recovery_authentication=self._verify_delivery_recovery_authentication,
            )
            self.message_forward = MessageForwardLedger(self._conn, self._lock)
            self._conn.commit()  # Schema writes create content-bearing WAL/SHM.
            self._restrict(f"{self.path}-wal")  # Sidecars inherit the process umask.
            self._restrict(f"{self.path}-shm")
            self.delivery.verify_replay()
        except BaseException:
            connection.close()
            raise

    @property
    def _conn(self) -> Any:
        """Expose the historical connection seam through the shared owner."""
        return self.storage.connection

    @_conn.setter
    def _conn(self, connection: Any) -> None:
        """Retain connection replacement semantics for existing owner extensions."""
        self.storage.connection = connection

    @property
    def encrypted(self) -> bool:
        """Return whether this store opened through SQLCipher page encryption."""
        return self._encrypted

    _restrict = staticmethod(restrict_file)

    @inherit_documentation(APPEND_DOCUMENTATION)
    def append(
        self, kind: str, payload: dict[str, Any], *, ts: float | None = None, durable: bool = False
    ) -> int:
        """Append one event to the log and return its assigned sequence number."""
        return self.append_batch(((kind, payload),), ts=ts, durable=durable)[0]

    @inherit_documentation(EventWriter.append_batch)
    def append_batch(
        self,
        events: Iterable[tuple[str, Mapping[str, Any]]],
        *,
        ts: float | None = None,
        durable: bool = False,
    ) -> tuple[int, ...]:
        """Append several events in one SQLite transaction."""
        return self._families.events.append_batch(events, ts=ts, durable=durable)

    _stored_operation = staticmethod(decode_operation)

    @inherit_documentation(OperationTable.get_operation)
    def get_operation(self, operation_key: str) -> StoredOperation | None:
        """Return one completed operation without exposing the key in errors."""
        return self._families.operations.get_operation(operation_key)

    @inherit_documentation(OperationTable.read_operations)
    def read_operations(self) -> tuple[OperationRecord, ...]:
        """Return durable operation records in commit order for cache seeding."""
        return self._families.operations.read_operations()

    @inherit_documentation(OperationTable.pending_operation_outbox_count)
    def pending_operation_outbox_count(self) -> int:
        """Return the number of committed operation intents awaiting projection."""
        return self._families.operations.pending_operation_outbox_count()

    @inherit_documentation(OperationTable.pending_operation_intents)
    def pending_operation_intents(self, *, limit: int = 100) -> tuple[PendingOperationIntent, ...]:
        """Return committed evidence intents awaiting idempotent local projection."""
        return self._families.operations.pending_operation_intents(limit=limit)

    @inherit_documentation(OperationTable.mark_operation_intent_delivered)
    def mark_operation_intent_delivered(self, operation_key: str, receipt_id: str) -> None:
        """Bind one operation intent to a value-free local projection receipt."""
        return self._families.operations.mark_operation_intent_delivered(operation_key, receipt_id)

    @inherit_documentation(ReceiptTable.commit_delivery_receipt_request)
    def commit_delivery_receipt_request(
        self,
        *,
        chat: Mapping[str, Any],
        requested: Mapping[str, Any],
        stage_hook: Callable[[str], None] | None = None,
    ) -> int:
        """Atomically commit a directed chat and its requested-receipt aggregate."""
        return self._families.receipts.commit_delivery_receipt_request(
            chat=chat, requested=requested, stage_hook=stage_hook
        )

    @inherit_documentation(ReceiptTable.backfill)
    def _backfill_delivery_receipt_aggregates(self) -> None:
        """Project legacy receipt events into the additive aggregate table once."""
        return self._families.receipts.backfill()

    @inherit_documentation(ReceiptTable.commit_delivery_receipt_transition)
    def commit_delivery_receipt_transition(
        self,
        *,
        kind: str,
        payload: Mapping[str, Any],
        notification: Mapping[str, Any],
        state: DeliveryReceiptState,
        companion: DeliveryReceiptTransition | None = None,
        stage_hook: Callable[[str], None] | None = None,
    ) -> str:
        """Commit one receipt transition and its stable notification outbox row."""
        return self._families.receipts.commit_delivery_receipt_transition(
            kind=kind,
            payload=payload,
            notification=notification,
            state=state,
            companion=companion,
            stage_hook=stage_hook,
        )

    _prepare_delivery_receipt_transition = staticmethod(prepare_transition)

    @inherit_documentation(ReceiptTable.insert_transition)
    def _insert_delivery_receipt_transition(
        self,
        transition: tuple[str, int, str, dict[str, Any], dict[str, Any], DeliveryReceiptState],
        *,
        stamp: float,
    ) -> None:
        """Insert one validated aggregate/event/outbox transition in the open transaction."""
        return self._families.receipts.insert_transition(transition, stamp=stamp)

    @inherit_documentation(ReceiptTable.delivery_receipt_aggregate)
    def delivery_receipt_aggregate(self, message_seq: int) -> DeliveryReceiptAggregate | None:
        """Return the current durable receipt aggregate for one chat sequence."""
        return self._families.receipts.delivery_receipt_aggregate(message_seq)

    @inherit_documentation(ReceiptTable.pending_delivery_notifications)
    def pending_delivery_notifications(
        self, *, sender: str | None = None, limit: int = 100
    ) -> tuple[PendingDeliveryNotification, ...]:
        """Return undelivered receipt frames in stable insertion order."""
        return self._families.receipts.pending_delivery_notifications(sender=sender, limit=limit)

    @inherit_documentation(ReceiptTable.mark_delivery_notification_attempt)
    def mark_delivery_notification_attempt(self, notification_id: str, *, delivered: bool) -> None:
        """Record one sender-delivery attempt and settle it only on transport success."""
        return self._families.receipts.mark_delivery_notification_attempt(
            notification_id, delivered=delivered
        )

    _json_object = staticmethod(encode_object)

    def _restore_normal_after_transaction(self, committed: bool) -> None:
        """Restore NORMAL sync without contradicting an already committed outcome."""
        self.storage.restore_normal(committed)

    @inherit_documentation(OperationTable.commit_operation)
    def commit_operation(
        self,
        *,
        operation_key: str,
        request_digest: str,
        response: Mapping[str, Any],
        events: Iterable[tuple[str, Mapping[str, Any]]],
        intent: Mapping[str, Any],
        response_event_seq_field: str | None = None,
        finalize_response: Callable[[dict[str, Any], tuple[int, ...]], Mapping[str, Any]]
        | None = None,
        max_retained_operations: int | None = None,
        required_predecessor: OperationRecord | None = None,
        stage_hook: Callable[[str], None] | None = None,
    ) -> OperationCommitResult:
        """Atomically commit a keyed mutation, exact response, and evidence intent."""
        return self._families.operations.commit_operation(
            operation_key=operation_key,
            request_digest=request_digest,
            response=response,
            events=events,
            intent=intent,
            response_event_seq_field=response_event_seq_field,
            finalize_response=finalize_response,
            max_retained_operations=max_retained_operations,
            required_predecessor=required_predecessor,
            stage_hook=stage_hook,
        )

    @inherit_documentation(EventWriter.pending_aef_events)
    def pending_aef_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        """Return queued legacy rows awaiting native AEF reconciliation."""
        return self._families.events.pending_aef_events(limit=limit)

    @inherit_documentation(EventWriter.mark_aef_delivered)
    def mark_aef_delivered(self, legacy_seq: int, receipt_id: str) -> None:
        """Durably bind one queued legacy row to its emitted AEF receipt."""
        return self._families.events.mark_aef_delivered(legacy_seq, receipt_id)

    @inherit_documentation(EventWriter.aef_delivery)
    def aef_delivery(self, legacy_seq: int) -> str | None:
        """Return the delivered receipt id, or ``None`` for pending/absent rows."""
        return self._families.events.aef_delivery(legacy_seq)

    @inherit_documentation(READ_ALL_DOCUMENTATION)
    def read_all(self) -> list[StoredEvent]:
        """Return every event in insertion order."""
        return list(self.iter_events())

    @inherit_documentation(EventAuthentication.ensure_mac_column)
    def _ensure_mac_column(self) -> None:
        """Add the row-authentication column to a log created before it existed."""
        return self._families.authentication.ensure_mac_column()

    @inherit_documentation(EventAuthentication.insert_event)
    def _insert_event_row(self, ts: float, kind: str, payload: str) -> int:
        """Insert one event row in the caller's transaction and return its sequence."""
        return self._families.authentication.insert_event(ts, kind, payload)

    @inherit_documentation(EventAuthentication.has_row_macs)
    def has_row_macs(self) -> bool:
        """Return whether any stored row carries a row MAC."""
        return self._families.authentication.has_row_macs()

    @inherit_documentation(EventAuthentication.verify_recovery_authentication)
    def _verify_delivery_recovery_authentication(self) -> None:
        """Refuse recovery without the original row key or with unauthenticated rows."""
        return self._families.authentication.verify_recovery_authentication()

    @inherit_documentation(EventAuthentication.enable_row_mac)
    def enable_row_mac(self, key: RowMacKey) -> tuple[CorruptEventRow, ...]:
        """Authenticate rows written from now on and quarantine unauthenticated ones."""
        return self._families.authentication.enable_row_mac(key)

    @property
    def row_quarantine(self) -> frozenset[int]:
        """Sequences quarantined because their row MAC failed."""
        return self._families.authentication.row_quarantine

    @inherit_documentation(EventAuthentication.decode_event)
    def _stored_event(
        self, row: tuple[object, object, object, object], *, apply_row_quarantine: bool = True
    ) -> StoredEvent:
        """Return one validated event or a non-secret corrupt-row marker."""
        return self._families.authentication.decode_event(
            row, apply_row_quarantine=apply_row_quarantine
        )

    @inherit_documentation(EventReader.iter_events)
    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
        after_seq: int | None = None,
        apply_row_quarantine: bool = True,
    ) -> Iterator[StoredEvent]:
        """Yield events in ascending sequence order without materialising the log."""
        return self._families.reads.iter_events(
            through_seq=through_seq,
            kinds=kinds,
            after_seq=after_seq,
            apply_row_quarantine=apply_row_quarantine,
        )

    @inherit_documentation(EventReader.read_since)
    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order."""
        return self._families.reads.read_since(after_seq, kinds=kinds, limit=limit)

    @inherit_documentation(EventReader.read_window)
    def read_window(
        self,
        *,
        min_seq: int | None = None,
        max_seq: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        kinds: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[StoredEvent]:
        """Return events inside an inclusive sequence/time window, in order."""
        return self._families.reads.read_window(
            min_seq=min_seq,
            max_seq=max_seq,
            since_ts=since_ts,
            until_ts=until_ts,
            kinds=kinds,
            limit=limit,
        )

    @inherit_documentation(EventReader.corrupt_rows)
    def corrupt_rows(self, *, through_seq: int | None = None) -> tuple[CorruptEventRow, ...]:
        """Return safe forensic markers for every malformed row in sequence order."""
        return self._families.reads.corrupt_rows(through_seq=through_seq)

    @inherit_documentation(EventReader.count)
    def count(self) -> int:
        """Return the number of events currently stored."""
        return self._families.reads.count()

    @inherit_documentation(EventReader.max_seq)
    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty."""
        return self._families.reads.max_seq()

    @inherit_documentation(EventReader.latest_at_or_before)
    def latest_at_or_before(self, through_seq: int) -> StoredEvent | None:
        """Return the newest retained event at or below ``through_seq``."""
        return self._families.reads.latest_at_or_before(through_seq)

    @inherit_documentation(EventMaintenance.delete)
    def delete(self, seqs: Iterable[int]) -> int:
        """Delete the events with these sequence numbers; return how many were removed."""
        return self._families.maintenance.delete(seqs)

    @inherit_documentation(EventMaintenance.vacuum)
    def vacuum(self) -> None:
        """Reclaim free pages left by deletes, shrinking the database file on disk."""
        return self._families.maintenance.vacuum()

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> EventStore:
        """Enter a context manager that closes the store on exit."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the store when leaving the context."""
        self.close()
