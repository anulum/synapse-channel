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

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NamedTuple

from synapse_channel.core.atomic_operations import OperationRecord
from synapse_channel.core.event_row_recovery import CorruptEventRow, decode_event_row

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


class EventStore:
    """Append-only SQLite event log in WAL mode.

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
        self.path = str(path)
        self._lock = threading.RLock()
        from synapse_channel.core.persistence_sqlcipher import connect_event_store

        self._conn, self._encrypted = connect_event_store(
            self.path,
            key=key,
            key_file=key_file,
            check_same_thread=False,
        )
        # The event log holds chat, findings, and recall telemetry, so restrict
        # it to the owner (0o600) where the platform supports it — encryption
        # does not replace permissions.
        self._restrict(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts REAL NOT NULL, "
            "kind TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS operations ("
            "operation_key TEXT PRIMARY KEY, "
            "request_digest TEXT NOT NULL, "
            "response_json TEXT NOT NULL, "
            "response_sha256 TEXT NOT NULL, "
            "first_event_seq INTEGER NOT NULL, "
            "commit_seq INTEGER NOT NULL, "
            "committed_at REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS operation_outbox ("
            "operation_key TEXT PRIMARY KEY, "
            "intent_json TEXT NOT NULL, "
            "receipt_id TEXT, "
            "FOREIGN KEY(operation_key) REFERENCES operations(operation_key))"
        )
        self._aef_outbox_kinds = frozenset(str(kind) for kind in aef_outbox_kinds)
        if self._aef_outbox_kinds:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS aef_outbox ("
                "legacy_seq INTEGER PRIMARY KEY, "
                "receipt_id TEXT UNIQUE, "
                "FOREIGN KEY(legacy_seq) REFERENCES events(seq))"
            )
        self._has_aef_outbox = (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'aef_outbox'"
            ).fetchone()
            is not None
        )
        self._conn.commit()
        # WAL mode creates ``-wal`` and ``-shm`` sidecars on the first write (the
        # ``CREATE TABLE`` commit above). They mirror the same content as the main
        # file but are born under the process umask, so lock them down once they exist.
        self._restrict(f"{self.path}-wal")
        self._restrict(f"{self.path}-shm")

    @property
    def encrypted(self) -> bool:
        """Return whether this store opened through SQLCipher page encryption."""
        return self._encrypted

    def _restrict(self, path: str) -> None:
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
            os.chmod(path, 0o600)

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        ts: float | None = None,
        durable: bool = False,
    ) -> int:
        """Append one event to the log and return its assigned sequence number.

        Parameters
        ----------
        kind : str
            Event kind tag.
        payload : dict[str, Any]
            JSON-serialisable event body.
        ts : float or None, optional
            Event timestamp, in seconds; the system clock is used when ``None``.
        durable : bool, optional
            When ``True`` the commit is synced at ``synchronous=FULL`` so it
            survives an OS crash; when ``False`` it commits at ``NORMAL`` (durable
            only against an application crash). Defaults to ``False``.

        Returns
        -------
        int
            The monotonic ``seq`` the row was assigned (the autoincrement primary
            key). It is durable and never reused across restarts — unlike the
            in-memory per-hub ``msg_id`` — so it is the stable cursor a reconnecting
            client resumes a directed-message backlog from.

        Notes
        -----
        A failed database write is rolled back. Durable attempts restore the
        connection to ``synchronous=NORMAL``. If that cleanup alone fails after
        COMMIT, the append remains successful and the cleanup failure is logged;
        reporting a write failure at that point would contradict durable truth.
        """
        return self.append_batch(((kind, payload),), ts=ts, durable=durable)[0]

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
            (
                stamp,
                str(kind),
                json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":")),
            )
            for kind, payload in events
        ]
        if not rows:
            return ()
        with self._lock:
            if durable:
                self._conn.execute("PRAGMA synchronous=FULL")
            try:
                sequences = []
                for row in rows:
                    cursor = self._conn.execute(
                        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", row
                    )
                    sequence = int(cursor.lastrowid or 0)
                    sequences.append(sequence)
                    if row[1] in self._aef_outbox_kinds:
                        self._conn.execute(
                            "INSERT INTO aef_outbox (legacy_seq, receipt_id) VALUES (?, NULL)",
                            (sequence,),
                        )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                if durable:
                    try:
                        self._conn.execute("PRAGMA synchronous=NORMAL")
                    except BaseException:
                        # The transaction outcome is already final. Reporting a
                        # post-commit cleanup error as append failure would make a
                        # caller roll back live state while the event remains
                        # durable. FULL is correctness-safe (only slower), so keep
                        # the committed outcome authoritative and make cleanup
                        # failure observable without inverting journal truth.
                        logger.exception("Could not restore SQLite synchronous=NORMAL")
        return tuple(sequences)

    @staticmethod
    def _stored_operation(row: tuple[object, ...]) -> StoredOperation:
        response = json.loads(str(row[2]))
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

    def get_operation(self, operation_key: str) -> StoredOperation | None:
        """Return one completed operation without exposing the key in errors."""
        with self._lock:
            row = self._conn.execute(
                "SELECT operation_key, request_digest, response_json, response_sha256, "
                "first_event_seq, commit_seq, committed_at FROM operations "
                "WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
        return None if row is None else self._stored_operation(row)

    def read_operations(self) -> tuple[OperationRecord, ...]:
        """Return durable operation records in commit order for cache seeding."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation_key, request_digest, response_json, response_sha256, "
                "first_event_seq, commit_seq, committed_at FROM operations "
                "ORDER BY commit_seq"
            ).fetchall()
        return tuple(
            OperationRecord(
                key=stored.operation_key,
                request_digest=stored.request_digest,
                response=stored.response,
            )
            for stored in (self._stored_operation(row) for row in rows)
        )

    def pending_operation_outbox_count(self) -> int:
        """Return the number of committed operation intents awaiting projection."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM operation_outbox WHERE receipt_id IS NULL"
            ).fetchone()
        return int(row[0])

    def pending_operation_intents(self, *, limit: int = 100) -> tuple[PendingOperationIntent, ...]:
        """Return committed evidence intents awaiting idempotent local projection."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 10_000:
            raise ValueError("operation outbox limit must be an integer from 1 through 10000")
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation_key, intent_json FROM operation_outbox "
                "WHERE receipt_id IS NULL ORDER BY rowid LIMIT ?",
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
        with self._lock:
            self._conn.execute("PRAGMA synchronous=FULL")
            try:
                cursor = self._conn.execute(
                    "UPDATE operation_outbox SET receipt_id = ? "
                    "WHERE operation_key = ? AND (receipt_id IS NULL OR receipt_id = ?)",
                    (receipt_id, operation_key, receipt_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError("operation evidence intent is absent or already settled")
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._conn.execute("PRAGMA synchronous=NORMAL")

    def commit_operation(
        self,
        *,
        operation_key: str,
        request_digest: str,
        response: Mapping[str, Any],
        events: Iterable[tuple[str, Mapping[str, Any]]],
        intent: Mapping[str, Any],
        response_event_seq_field: str | None = None,
        stage_hook: Callable[[str], None] | None = None,
    ) -> OperationCommitResult:
        """Atomically commit a keyed mutation, exact response, and evidence intent."""
        if not operation_key:
            raise ValueError("atomic operation key must be non-empty")
        if len(request_digest) != 64 or any(
            character not in "0123456789abcdef" for character in request_digest
        ):
            raise ValueError("atomic operation request digest must be lowercase SHA-256")
        stamp = time.time()
        event_rows = [
            (
                stamp,
                str(kind),
                json.dumps(
                    dict(payload),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
            for kind, payload in events
        ]
        if not event_rows:
            raise ValueError("atomic operation requires at least one mutation event")
        response_value = dict(response)
        response_json = json.dumps(
            response_value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        intent_json = json.dumps(
            dict(intent),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        hook = stage_hook or (lambda _stage: None)

        with self._lock:
            self._conn.execute("PRAGMA synchronous=FULL")
            committed = False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    "SELECT operation_key, request_digest, response_json, response_sha256, "
                    "first_event_seq, commit_seq, committed_at FROM operations "
                    "WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if existing is not None:
                    self._conn.rollback()
                    stored = self._stored_operation(existing)
                    outcome: Literal["replayed", "conflict"] = (
                        "replayed" if stored.request_digest == request_digest else "conflict"
                    )
                    return OperationCommitResult(outcome, stored)

                sequences: list[int] = []
                for row in event_rows:
                    cursor = self._conn.execute(
                        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", row
                    )
                    sequence = int(cursor.lastrowid or 0)
                    sequences.append(sequence)
                    if row[1] in self._aef_outbox_kinds:
                        self._conn.execute(
                            "INSERT INTO aef_outbox (legacy_seq, receipt_id) VALUES (?, NULL)",
                            (sequence,),
                        )
                    hook("after_legacy_event_insert")

                if response_event_seq_field is not None:
                    response_value[response_event_seq_field] = sequences[0]
                    response_json = json.dumps(
                        response_value,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                response_sha256 = hashlib.sha256(response_json.encode("ascii")).hexdigest()
                first_seq = sequences[0]
                compatibility = {
                    "key": operation_key,
                    "request_digest": request_digest,
                    "response": response_value,
                    "response_sha256": response_sha256,
                    "first_event_seq": first_seq,
                    "commit_seq": sequences[-1] + 1,
                }
                cursor = self._conn.execute(
                    "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                    (
                        stamp,
                        "idempotency",
                        json.dumps(
                            compatibility,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ),
                )
                commit_seq = int(cursor.lastrowid or 0)
                self._conn.execute(
                    "INSERT INTO operations (operation_key, request_digest, response_json, "
                    "response_sha256, first_event_seq, commit_seq, committed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_key,
                        request_digest,
                        response_json,
                        response_sha256,
                        first_seq,
                        commit_seq,
                        stamp,
                    ),
                )
                hook("after_operation_insert")
                self._conn.execute(
                    "INSERT INTO operation_outbox (operation_key, intent_json, receipt_id) "
                    "VALUES (?, ?, NULL)",
                    (operation_key, intent_json),
                )
                hook("after_operation_outbox_insert")
                hook("before_commit")
                self._conn.commit()
                committed = True
                hook("after_commit")
                return OperationCommitResult(
                    "inserted",
                    StoredOperation(
                        operation_key,
                        request_digest,
                        response_value,
                        response_sha256,
                        first_seq,
                        commit_seq,
                        stamp,
                    ),
                )
            except BaseException:
                if not committed:
                    self._conn.rollback()
                raise
            finally:
                try:
                    self._conn.execute("PRAGMA synchronous=NORMAL")
                except BaseException:
                    if not committed:
                        raise
                    logger.exception("Could not restore SQLite synchronous=NORMAL")

    def pending_aef_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        """Return queued legacy rows awaiting native AEF reconciliation."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 10_000:
            raise ValueError("AEF outbox limit must be an integer from 1 through 10000")
        if not self._has_aef_outbox:
            return ()
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.seq, e.ts, e.kind, e.payload "
                "FROM aef_outbox AS o JOIN events AS e ON e.seq = o.legacy_seq "
                "WHERE o.receipt_id IS NULL ORDER BY e.seq LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._stored_event(row) for row in rows)

    def mark_aef_delivered(self, legacy_seq: int, receipt_id: str) -> None:
        """Durably bind one queued legacy row to its emitted AEF receipt."""
        if isinstance(legacy_seq, bool) or not isinstance(legacy_seq, int) or legacy_seq < 1:
            raise ValueError("AEF outbox sequence must be a positive integer")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError("AEF outbox receipt id must be non-empty text")
        with self._lock:
            self._conn.execute("PRAGMA synchronous=FULL")
            try:
                row = self._conn.execute(
                    "SELECT receipt_id FROM aef_outbox WHERE legacy_seq = ?", (legacy_seq,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"legacy sequence {legacy_seq} is not queued for AEF")
                current = row[0]
                if current is not None and str(current) != receipt_id:
                    raise ValueError("AEF outbox sequence is already bound to another receipt")
                self._conn.execute(
                    "UPDATE aef_outbox SET receipt_id = ? WHERE legacy_seq = ?",
                    (receipt_id, legacy_seq),
                )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._conn.execute("PRAGMA synchronous=NORMAL")

    def aef_delivery(self, legacy_seq: int) -> str | None:
        """Return the delivered receipt id, or ``None`` for pending/absent rows."""
        if not self._has_aef_outbox:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_id FROM aef_outbox WHERE legacy_seq = ?", (legacy_seq,)
            ).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def read_all(self) -> list[StoredEvent]:
        """Return every event in insertion order.

        Returns
        -------
        list[StoredEvent]
            All persisted events, ordered by ascending sequence number.
        """
        return list(self.iter_events())

    @staticmethod
    def _stored_event(row: tuple[object, object, object, object]) -> StoredEvent:
        """Return one validated event or a non-secret corrupt-row marker."""
        decoded = decode_event_row(row)
        return StoredEvent(
            seq=decoded.seq,
            ts=decoded.ts,
            kind=decoded.kind,
            payload=decoded.payload,
        )

    def iter_events(
        self,
        *,
        through_seq: int | None = None,
        kinds: Iterable[str] | None = None,
    ) -> Iterator[StoredEvent]:
        """Yield events in ascending sequence order without materialising the log.

        This is the bounded-memory read seam for whole-log folds (the Merkle
        commitment, causality reconstruction): rows stream off the SQLite cursor
        one at a time, so the peak footprint is one event, not the log. A kind
        filter is applied inside SQLite, so uninterested kinds (bulk chat on a
        long-lived hub) never cross into Python at all.

        Parameters
        ----------
        through_seq : int or None, optional
            Inclusive sequence ceiling; events after it are not yielded. ``None``
            streams the whole log.
        kinds : Iterable[str] or None, optional
            When given, restrict the stream to these event kinds; an empty
            iterable yields nothing. ``None`` streams every kind.

        Yields
        ------
        StoredEvent
            Each matching event at or below the ceiling, by ascending sequence.
        """
        sql = "SELECT seq, ts, kind, payload FROM events"
        clauses: list[str] = []
        params: list[Any] = []
        if through_seq is not None:
            clauses.append("seq <= ?")
            params.append(int(through_seq))
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return
            clauses.append(f"kind IN ({','.join('?' for _ in kind_list)})")
            params.extend(kind_list)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"
        with self._lock:
            for row in self._conn.execute(sql, params):
                yield self._stored_event(row)

    def read_since(
        self,
        after_seq: int,
        *,
        kinds: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order.

        This is the durable, presence-free ingest seam a downstream
        persistent-memory adapter polls: it tracks the last sequence it consumed,
        calls :meth:`read_since` with it, processes the batch, and advances —
        resuming with no loss or duplication across hub restarts, because the
        sequence is a monotonic primary key.

        Parameters
        ----------
        after_seq : int
            Exclusive lower bound; only events with ``seq > after_seq`` are
            returned. Pass ``0`` for the whole log.
        kinds : Iterable[str] or None, optional
            When given, restrict the result to these event kinds (e.g.
            :data:`~synapse_channel.core.journal.MEMORY_KINDS`); an empty iterable
            returns nothing. ``None`` returns every kind.
        limit : int or None, optional
            Cap the batch size (floored at ``0``); ``None`` returns all matching
            events. The cap applies after ordering, so repeated calls walk the log
            forward in fixed-size batches.

        Returns
        -------
        list[StoredEvent]
            Matching events ordered by ascending sequence number.
        """
        sql = "SELECT seq, ts, kind, payload FROM events WHERE seq > ?"
        params: list[Any] = [int(after_seq)]
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return []
            sql += f" AND kind IN ({','.join('?' for _ in kind_list)})"
            params.extend(kind_list)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._stored_event(row) for row in rows]

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
        """Return events inside an inclusive sequence/time window, in order.

        This is the selective-read seam the event-query layer uses to avoid
        loading an unbounded event store for every point-in-time or windowed
        query: the bounds are pushed into SQLite so only candidate rows are
        deserialised. Every bound is optional and inclusive; omitting all of them
        is equivalent to :meth:`read_all`.

        Parameters
        ----------
        min_seq, max_seq : int or None, optional
            Inclusive lower and upper sequence bounds (``seq >= min_seq`` /
            ``seq <= max_seq``).
        since_ts, until_ts : float or None, optional
            Inclusive lower and upper timestamp bounds (``ts >= since_ts`` /
            ``ts <= until_ts``).
        kinds : Iterable[str] or None, optional
            Restrict to these event kinds; an empty iterable returns nothing.
        limit : int or None, optional
            Cap the number of rows returned after ordering (floored at ``0``).

        Returns
        -------
        list[StoredEvent]
            Matching events ordered by ascending sequence number.
        """
        sql = "SELECT seq, ts, kind, payload FROM events WHERE 1 = 1"
        params: list[Any] = []
        if min_seq is not None:
            sql += " AND seq >= ?"
            params.append(int(min_seq))
        if max_seq is not None:
            sql += " AND seq <= ?"
            params.append(int(max_seq))
        if since_ts is not None:
            sql += " AND ts >= ?"
            params.append(float(since_ts))
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(float(until_ts))
        if kinds is not None:
            kind_list = [str(k) for k in kinds]
            if not kind_list:
                return []
            sql += f" AND kind IN ({','.join('?' for _ in kind_list)})"
            params.extend(kind_list)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._stored_event(row) for row in rows]

    def corrupt_rows(self, *, through_seq: int | None = None) -> tuple[CorruptEventRow, ...]:
        """Return safe forensic markers for every malformed row in sequence order.

        Parameters
        ----------
        through_seq : int or None, optional
            Inclusive sequence ceiling. ``None`` scans the complete event log.

        Returns
        -------
        tuple[CorruptEventRow, ...]
            Markers contain reasons and a raw-payload digest, never raw payload
            bytes. This scan is the operator seam used by explicit compaction.
        """
        sql = "SELECT seq, ts, kind, payload FROM events"
        params: tuple[int, ...] = ()
        if through_seq is not None:
            sql += " WHERE seq <= ?"
            params = (int(through_seq),)
        sql += " ORDER BY seq"
        corrupt: list[CorruptEventRow] = []
        with self._lock:
            for row in self._conn.execute(sql, params):
                marker = decode_event_row(row).corruption
                if marker is not None:
                    corrupt.append(marker)
        return tuple(corrupt)

    def count(self) -> int:
        """Return the number of events currently stored."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty.

        Useful as a fully-settled compaction floor: with no read-side consumer
        lagging behind, the whole log up to the latest sequence may be compacted
        (see :mod:`synapse_channel.core.compaction`).
        """
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return int(row[0])

    def latest_at_or_before(self, through_seq: int) -> StoredEvent | None:
        """Return the newest retained event at or below ``through_seq``.

        A direct descending primary-key lookup keeps state-at projections from
        decoding the whole journal merely to obtain their deterministic clock.
        Sequence gaps left by retention are handled by selecting the preceding
        retained event; an empty prefix returns ``None``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, ts, kind, payload FROM events "
                "WHERE seq <= ? ORDER BY seq DESC LIMIT 1",
                (int(through_seq),),
            ).fetchone()
        return None if row is None else self._stored_event(row)

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
        sql = "DELETE FROM events WHERE seq = ?"
        if self._has_aef_outbox:
            # The outbox is the recovery boundary between the authoritative
            # legacy commit and its native receipt. Compaction may remove a
            # settled legacy row, but it must never erase a still-pending source
            # event and make the durable cursor silently disappear from the
            # join used by pending_aef_events(). This guard is explicit rather
            # than dependent on SQLite foreign-key enforcement, whose global
            # activation would change historical compaction behaviour.
            sql += (
                " AND NOT EXISTS (SELECT 1 FROM aef_outbox "
                "WHERE legacy_seq = events.seq AND receipt_id IS NULL)"
            )
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM operations "
            "WHERE events.seq BETWEEN operations.first_event_seq AND operations.commit_seq)"
        )
        with self._lock:
            cursor = self._conn.executemany(sql, ((seq,) for seq in seq_list))
            self._conn.commit()
        return int(cursor.rowcount)

    def vacuum(self) -> None:
        """Reclaim free pages left by deletes, shrinking the database file on disk.

        A ``DELETE`` marks pages free for reuse but does not return them to the
        filesystem, so a large retention sweep leaves the file the same size until
        ``VACUUM`` rewrites the database to release the free pages. It rewrites the
        whole database, so call it from a maintenance path, not the hot loop.
        """
        with self._lock:
            self._conn.commit()  # VACUUM cannot run inside an open transaction
            self._conn.execute("VACUUM")

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
