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
on start-up (see :mod:`synapse_channel.journal`).

Durability is split honestly to match the workload. The connection runs at
``synchronous=NORMAL``, which is durable against a **process/application crash**
but may lose the most recent commit on an **OS crash or power loss**. A write
marked ``durable=True`` — the lease/claim path — is committed at
``synchronous=FULL`` so it survives an OS crash too; the high-volume chat/history
path stays at ``NORMAL``. This module never claims more than it delivers.
"""

from __future__ import annotations

import json
import sqlite3
import time
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
        Event kind tag (see :class:`synapse_channel.journal.EventKind`).
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
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
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

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        ts: float | None = None,
        durable: bool = False,
    ) -> None:
        """Append one event to the log.

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
        """
        stamp = time.time() if ts is None else float(ts)
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if durable:
            self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (stamp, kind, raw)
        )
        self._conn.commit()
        if durable:
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def read_all(self) -> list[StoredEvent]:
        """Return every event in insertion order.

        Returns
        -------
        list[StoredEvent]
            All persisted events, ordered by ascending sequence number.
        """
        rows = self._conn.execute(
            "SELECT seq, ts, kind, payload FROM events ORDER BY seq"
        ).fetchall()
        return [
            StoredEvent(seq=int(seq), ts=float(ts), kind=str(kind), payload=json.loads(payload))
            for seq, ts, kind, payload in rows
        ]

    def count(self) -> int:
        """Return the number of events currently stored."""
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0])

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
