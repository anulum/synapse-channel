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
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple

BUSY_TIMEOUT_MS = 5000


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
    ) -> None:
        self.path = str(path)
        from synapse_channel.core.persistence_sqlcipher import connect_event_store

        self._conn, self._encrypted = connect_event_store(self.path, key=key, key_file=key_file)
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
        connection to ``synchronous=NORMAL`` before the failure propagates.
        """
        stamp = time.time() if ts is None else float(ts)
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if durable:
            self._conn.execute("PRAGMA synchronous=FULL")
        try:
            cursor = self._conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (stamp, kind, raw)
            )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            if durable:
                self._conn.execute("PRAGMA synchronous=NORMAL")
        return int(cursor.lastrowid or 0)

    def read_all(self) -> list[StoredEvent]:
        """Return every event in insertion order.

        Returns
        -------
        list[StoredEvent]
            All persisted events, ordered by ascending sequence number.
        """
        return list(self.iter_events())

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
        for seq, ts, kind, payload in self._conn.execute(sql, params):
            yield StoredEvent(
                seq=int(seq), ts=float(ts), kind=str(kind), payload=json.loads(payload)
            )

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
        rows = self._conn.execute(sql, params).fetchall()
        return [
            StoredEvent(seq=int(seq), ts=float(ts), kind=str(kind), payload=json.loads(payload))
            for seq, ts, kind, payload in rows
        ]

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
        rows = self._conn.execute(sql, params).fetchall()
        return [
            StoredEvent(seq=int(seq), ts=float(ts), kind=str(kind), payload=json.loads(payload))
            for seq, ts, kind, payload in rows
        ]

    def count(self) -> int:
        """Return the number of events currently stored."""
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

    def max_seq(self) -> int:
        """Return the highest sequence number stored, or ``0`` when the log is empty.

        Useful as a fully-settled compaction floor: with no read-side consumer
        lagging behind, the whole log up to the latest sequence may be compacted
        (see :mod:`synapse_channel.core.compaction`).
        """
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return int(row[0])

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
        cursor = self._conn.executemany(
            "DELETE FROM events WHERE seq = ?",
            ((seq,) for seq in seq_list),
        )
        self._conn.commit()
        return int(cursor.rowcount)

    def vacuum(self) -> None:
        """Reclaim free pages left by deletes, shrinking the database file on disk.

        A ``DELETE`` marks pages free for reuse but does not return them to the
        filesystem, so a large retention sweep leaves the file the same size until
        ``VACUUM`` rewrites the database to release the free pages. It rewrites the
        whole database, so call it from a maintenance path, not the hot loop.
        """
        self._conn.commit()  # VACUUM cannot run inside an open transaction
        self._conn.execute("VACUUM")

    def close(self) -> None:
        """Close the underlying database connection."""
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
