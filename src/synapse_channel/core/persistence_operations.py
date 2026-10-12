# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — append-only SQLite event store for durable hub state
"""OperationTable implementation for the existing EventStore facade."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.persistence_connection import StoreConnection

if TYPE_CHECKING:
    from synapse_channel.core.persistence import (
        OperationCommitResult,
        PendingOperationIntent,
        StoredOperation,
    )
logger = logging.getLogger("synapse_channel.core.persistence")


def decode_operation(row: tuple[object, ...]) -> StoredOperation:
    """Decode the original exact-response operation record.

    Parameters
    ----------
    row : tuple
        The ordered seven columns from the operations table.

    Returns
    -------
    StoredOperation
        The original public named tuple, with a JSON-object response.
    """
    response = json.loads(str(row[2]))
    from synapse_channel.core.persistence import StoredOperation

    if not isinstance(response, dict):
        raise ValueError("stored operation response must be a JSON object")
    return StoredOperation(
        operation_key=str(row[0]),
        request_digest=str(row[1]),
        response=response,
        response_sha256=str(row[3]),
        first_event_seq=int(str(row[4])),
        commit_seq=int(str(row[5])),
        committed_at=float(str(row[6])),
    )


class OperationTable:
    """Own this table family under the shared connection and lock."""

    def __init__(self, storage: StoreConnection) -> None:
        """Bind the existing owner connection without opening a second one."""
        self.storage = storage

    def get_operation(self, operation_key: str) -> StoredOperation | None:
        """Return one completed operation without exposing the key in errors."""
        with self.storage.lock:
            row = self.storage.connection.execute(
                (
                    "SELECT operation_key, request_digest, response_json, response_sha256, "
                    "first_event_seq, commit_seq, committed_at FROM operations WHERE "
                    "operation_key = "
                    "?"
                ),
                (operation_key,),
            ).fetchone()
        return None if row is None else decode_operation(row)

    def read_operations(self) -> tuple[OperationRecord, ...]:
        """Return durable operation records in commit order for cache seeding."""
        with self.storage.lock:
            rows = self.storage.connection.execute(
                "SELECT operation_key, request_digest, response_json, response_sha256, "
                "first_event_seq, commit_seq, committed_at FROM operations ORDER BY "
                "commit_seq"
            ).fetchall()
        return tuple(
            OperationRecord(
                key=stored.operation_key,
                request_digest=stored.request_digest,
                response=stored.response,
            )
            for stored in (decode_operation(row) for row in rows)
        )

    def pending_operation_outbox_count(self) -> int:
        """Return the number of committed operation intents awaiting projection."""
        with self.storage.lock:
            row = self.storage.connection.execute(
                "SELECT COUNT(*) FROM operation_outbox WHERE receipt_id IS NULL"
            ).fetchone()
        return int(row[0])

    def pending_operation_intents(self, *, limit: int = 100) -> tuple[PendingOperationIntent, ...]:
        """Return committed evidence intents awaiting idempotent local projection."""
        from synapse_channel.core.persistence import PendingOperationIntent

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or (limit > 10000):
            raise ValueError("operation outbox limit must be an integer from 1 through 10000")
        with self.storage.lock:
            rows = self.storage.connection.execute(
                (
                    "SELECT operation_key, intent_json FROM operation_outbox WHERE "
                    "receipt_id IS NULL ORDER BY rowid LIMIT "
                    "?"
                ),
                (limit,),
            ).fetchall()
        intents: list[PendingOperationIntent] = []
        for key, encoded in rows:
            intent = json.loads(str(encoded))
            if not isinstance(intent, dict):
                raise ValueError("stored operation intent must be a JSON object")
            intents.append(PendingOperationIntent(str(key), intent))
        return tuple(intents)

    def mark_operation_intent_delivered(self, operation_key: str, receipt_id: str) -> None:
        """Bind one operation intent to a value-free local projection receipt."""
        if not operation_key or not receipt_id:
            raise ValueError("operation outbox identity and receipt must be non-empty")
        with self.storage.lock:
            self.storage.connection.execute("PRAGMA synchronous=FULL")
            try:
                cursor = self.storage.connection.execute(
                    (
                        "UPDATE operation_outbox SET receipt_id = ? WHERE operation_key = ? "
                        "AND (receipt_id IS NULL OR receipt_id = "
                        "?)"
                    ),
                    (receipt_id, operation_key, receipt_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError("operation evidence intent is absent or already settled")
                self.storage.connection.commit()
            except BaseException:
                self.storage.connection.rollback()
                raise
            finally:
                self.storage.connection.execute("PRAGMA synchronous=NORMAL")

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
        """Atomically commit a keyed mutation, exact response, and evidence intent.

        Parameters
        ----------
        operation_key:
            Already authenticated and namespaced idempotency key.
        request_digest:
            Exact semantic request SHA-256.
        response:
            JSON response draft, unchanged by this method.
        events:
            Nonempty ordered mutation events committed with the response.
        intent:
            Evidence-outbox payload committed in the same transaction.
        response_event_seq_field:
            Legacy top-level response field receiving the first event sequence.
        finalize_response:
            Optional trusted synchronous builder called with a private JSON copy
            of the draft and actual mutation-event sequences. It can bind nested
            fields and sign the final response before persistence. It must not
            perform external I/O, re-enter this store or mutate live state.
            Exceptions roll back all pending rows. It is never called on replay
            or conflict and cannot accompany ``response_event_seq_field``.
        stage_hook:
            Optional transaction-boundary observer used by failure tests.
        max_retained_operations:
            Optional explicit store-wide cap checked under BEGIN IMMEDIATE before
            inserting new history. Existing replay/conflict remains readable when
            the cap is full; no retained operation is evicted to make room.
        required_predecessor:
            Optional exact completed operation required for a new insertion.
            Key, non-legacy digest, canonical response and hash are checked in
            the same transaction before any event insertion. Replay remains a
            historical response, not renewed authorization under this condition.

        Returns
        -------
        OperationCommitResult
            Inserted, replayed or conflicting durable response and sequence IDs.

        Raises
        ------
        ValueError
            For invalid identities, empty events, incompatible response builders
            or a response that is not serialisable as finite JSON.
        """
        from synapse_channel.core.persistence_operation_commit import (
            commit_prepared,
            prepare_operation,
        )

        return commit_prepared(
            self.storage,
            prepare_operation(
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
            ),
        )
