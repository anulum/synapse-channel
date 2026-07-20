# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable per-message-auth replay and sequence floor
"""Crash-durable nonce replay and optional sequence floor for message auth.

This store is independent of the AEF receipt index and the legacy event journal.
It closes the hub-restart hole where an in-memory nonce cache is wiped while a
captured signed frame remains inside the timestamp window.

Present (default) semantics elsewhere still treat ``sequence`` as signed
metadata only. When this store is attached:

* **nonce identity** ``(key_id, sender, nonce)`` is the durable replay key;
* **sequence floor** is opt-in via :class:`SequenceFloorMode` and is never
  enabled merely by opening a durable store path.

Modes
-----
``off``
    Durable nonces only. Sequence is stored for diagnostics and for later mode
    upgrades; it is not used to refuse frames.
``compat``
    Durable nonces; the highest accepted sequence per ``(key_id, sender)`` is
    advanced. A *lower* sequence with a *new* nonce is still admitted so a
    client that resets its counter on reconnect does not fail closed. Same
    nonce remains a replay.
``strict``
    Durable nonces plus a hard floor: any sequence less than or equal to the
    stored floor for that ``(key_id, sender)`` is refused as
    ``sequence_mismatch``. Operators must keep client counters monotonic
    across restarts when enabling this mode.

All admissions use ``BEGIN IMMEDIATE`` and ``synchronous=FULL`` so concurrent
processes cannot both accept the same nonce.
"""

from __future__ import annotations

import contextlib
import os
import threading
from enum import Enum
from pathlib import Path
from types import TracebackType

from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.persistence_sqlcipher import connect_event_store


class SequenceFloorMode(str, Enum):
    """How strictly the durable store enforces per-sender sequence floors."""

    OFF = "off"
    COMPAT = "compat"
    STRICT = "strict"


class DurableAdmitResult(str, Enum):
    """Outcome of one atomic durable admission attempt."""

    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    CAPACITY = "capacity"


class DurableMessageAuthReplayStore:
    """SQLite-backed nonce ledger and optional sequence floor for message auth.

    Parameters
    ----------
    path :
        SQLite database path. Use a dedicated file; do not share table names
        with the legacy event journal or AEF index (this module creates its own
        tables only).
    max_entries :
        Maximum retained nonce rows after timestamp eviction. When the live
        window is full, new nonces are refused rather than evicting in-window
        identities (same capacity policy as the in-memory cache).
    window_seconds :
        Nonce retention age matched to the verification timestamp window.
    key_file, key :
        Optional SQLCipher key material. The replay ledger follows the same
        encrypted-at-rest posture as the authoritative hub journal when the
        CLI derives both stores from ``--db-key-file``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int,
        window_seconds: float,
        key_file: str | Path | None = None,
        key: bytes | None = None,
    ) -> None:
        self.path = str(path)
        self.max_entries = max(int(max_entries), 1)
        self.window_seconds = max(float(window_seconds), 0.001)
        self._lock = threading.Lock()
        if self.path != ":memory:" and not self.path.startswith("file:"):
            parent = Path(self.path).expanduser().resolve().parent
            parent.mkdir(parents=True, exist_ok=True)
        self._conn, self._encrypted = connect_event_store(
            self.path,
            key=key,
            key_file=key_file,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS message_auth_nonces ("
            "key_id TEXT NOT NULL, "
            "sender TEXT NOT NULL, "
            "nonce TEXT NOT NULL, "
            "sequence INTEGER NOT NULL CHECK(sequence >= 1), "
            "auth_timestamp REAL NOT NULL, "
            "PRIMARY KEY(key_id, sender, nonce))"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_auth_nonces_ts "
            "ON message_auth_nonces(auth_timestamp)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS message_auth_sequence_floors ("
            "key_id TEXT NOT NULL, "
            "sender TEXT NOT NULL, "
            "floor_sequence INTEGER NOT NULL CHECK(floor_sequence >= 1), "
            "updated_at REAL NOT NULL, "
            "PRIMARY KEY(key_id, sender))"
        )
        self._restrict(self.path)
        self._restrict(f"{self.path}-wal")
        self._restrict(f"{self.path}-shm")

    @property
    def encrypted(self) -> bool:
        """Return whether SQLCipher protects this replay ledger at rest."""
        return self._encrypted

    def admit(
        self,
        *,
        key_id: str,
        sender: str,
        nonce: str,
        sequence: int,
        timestamp: float,
        now: float,
        mode: SequenceFloorMode = SequenceFloorMode.OFF,
    ) -> DurableAdmitResult:
        """Atomically admit one authenticated identity or classify the refusal.

        Parameters
        ----------
        key_id, sender, nonce :
            Replay identity triple. ``sequence`` is never part of the nonce key.
        sequence :
            Positive signed sequence metadata from the frame.
        timestamp :
            Authentication timestamp carried on the frame.
        now :
            Verifier wall-clock time used for eviction and floor bookkeeping.
        mode :
            Sequence-floor policy for this admission.

        Returns
        -------
        DurableAdmitResult
            ``accepted`` only when the row is committed. Database failures
            propagate and must be treated as fail-closed by the caller.
        """
        if not key_id or not sender or not nonce:
            raise ValueError("durable message-auth identity fields must be non-empty")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("durable message-auth sequence must be a positive integer")
        mode_value = SequenceFloorMode(mode)
        auth_ts = float(timestamp)
        now_float = float(now)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cutoff = now_float - self.window_seconds
                self._conn.execute(
                    "DELETE FROM message_auth_nonces WHERE auth_timestamp < ?",
                    (cutoff,),
                )
                existing = self._conn.execute(
                    "SELECT sequence FROM message_auth_nonces "
                    "WHERE key_id = ? AND sender = ? AND nonce = ?",
                    (key_id, sender, nonce),
                ).fetchone()
                if existing is not None:
                    self._conn.rollback()
                    return DurableAdmitResult.REPLAYED
                if mode_value is SequenceFloorMode.STRICT:
                    floor_row = self._conn.execute(
                        "SELECT floor_sequence FROM message_auth_sequence_floors "
                        "WHERE key_id = ? AND sender = ?",
                        (key_id, sender),
                    ).fetchone()
                    if floor_row is not None and sequence <= int(floor_row[0]):
                        self._conn.rollback()
                        return DurableAdmitResult.SEQUENCE_MISMATCH
                live_count = int(
                    self._conn.execute("SELECT COUNT(*) FROM message_auth_nonces").fetchone()[0]
                )
                if live_count >= self.max_entries:
                    self._conn.rollback()
                    return DurableAdmitResult.CAPACITY
                self._conn.execute(
                    "INSERT INTO message_auth_nonces "
                    "(key_id, sender, nonce, sequence, auth_timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key_id, sender, nonce, sequence, auth_ts),
                )
                if mode_value is not SequenceFloorMode.OFF:
                    floor_row = self._conn.execute(
                        "SELECT floor_sequence FROM message_auth_sequence_floors "
                        "WHERE key_id = ? AND sender = ?",
                        (key_id, sender),
                    ).fetchone()
                    if floor_row is None or sequence > int(floor_row[0]):
                        self._conn.execute(
                            "INSERT INTO message_auth_sequence_floors "
                            "(key_id, sender, floor_sequence, updated_at) "
                            "VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(key_id, sender) DO UPDATE SET "
                            "floor_sequence = excluded.floor_sequence, "
                            "updated_at = excluded.updated_at",
                            (key_id, sender, sequence, now_float),
                        )
                self._conn.commit()
            except BaseException:
                with contextlib.suppress(BaseException):
                    self._conn.rollback()
                raise
        return DurableAdmitResult.ACCEPTED

    def floor(self, key_id: str, sender: str) -> int | None:
        """Return the durable sequence floor for ``(key_id, sender)``, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT floor_sequence FROM message_auth_sequence_floors "
                "WHERE key_id = ? AND sender = ?",
                (key_id, sender),
            ).fetchone()
        if row is None:
            return None
        return int(row[0])

    def nonce_count(self) -> int:
        """Return the number of retained durable nonces."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM message_auth_nonces").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> DurableMessageAuthReplayStore:
        """Return this open store for a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store when leaving a context manager."""
        self.close()

    @staticmethod
    def _restrict(path: str) -> None:
        if path == ":memory:" or path.startswith("file:"):
            return
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
