# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable AEF replay and chain-conflict index
"""Crash-durable replay and sequence-conflict classification for AEF receipts.

The index is a read-side verification ledger, not an event source. It may share
the hub's SQLite file, but it never inserts into, reinterprets, or joins the
legacy ``events`` table. Each new identity is committed with
``synchronous=FULL`` inside ``BEGIN IMMEDIATE`` so concurrent processes cannot
both accept a replay or an equivocation at the same ``(log_id, seq)``.
"""

from __future__ import annotations

import contextlib
import re
import threading
from pathlib import Path
from types import TracebackType

from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.persistence_sqlcipher import connect_event_store

_LOG_ID = re.compile(r"[0-9a-f]{64}")
_RECEIPT_ID = re.compile(r"aef1:[0-9a-f]{64}")


class AefDurableReceiptIndex:
    """SQLite-backed AEF replay/conflict index with restart-safe decisions.

    Parameters
    ----------
    path:
        SQLite database path. This may be the hub event-store path: the AEF
        index uses its own table and does not touch legacy event rows.
    key_file, key:
        Optional SQLCipher material with the same semantics as
        :class:`~synapse_channel.core.persistence.EventStore`.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        key_file: str | Path | None = None,
        key: bytes | None = None,
    ) -> None:
        self.path = str(path)
        self._conn, self._encrypted = connect_event_store(self.path, key=key, key_file=key_file)
        self._lock = threading.Lock()
        self._restrict(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS aef_receipt_index ("
            "log_id TEXT NOT NULL, "
            "seq INTEGER NOT NULL CHECK(seq >= 1), "
            "receipt_id TEXT NOT NULL, "
            "PRIMARY KEY(log_id, seq), "
            "UNIQUE(log_id, receipt_id))"
        )
        self._conn.commit()
        self._restrict(f"{self.path}-wal")
        self._restrict(f"{self.path}-shm")

    @property
    def encrypted(self) -> bool:
        """Return whether the index opened through SQLCipher."""
        return self._encrypted

    def classify_and_remember(
        self, log_id: str, seq: int, receipt_id: str
    ) -> AefVerdictCode | None:
        """Atomically persist a new identity or classify an existing one.

        A committed insert returning ``None`` is the only acceptance outcome.
        Read-only replay/conflict outcomes roll back their reserved transaction;
        database failures propagate without being misreported as acceptance.
        """
        self._validate_identity(log_id, seq, receipt_id)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                by_sequence = self._conn.execute(
                    "SELECT receipt_id FROM aef_receipt_index WHERE log_id = ? AND seq = ?",
                    (log_id, seq),
                ).fetchone()
                if by_sequence is not None:
                    self._conn.rollback()
                    if str(by_sequence[0]) == receipt_id:
                        return AefVerdictCode.REPLAYED
                    return AefVerdictCode.CHAIN_CONFLICT
                by_receipt = self._conn.execute(
                    "SELECT seq FROM aef_receipt_index WHERE log_id = ? AND receipt_id = ?",
                    (log_id, receipt_id),
                ).fetchone()
                if by_receipt is not None:
                    self._conn.rollback()
                    return AefVerdictCode.REPLAYED
                self._conn.execute(
                    "INSERT INTO aef_receipt_index (log_id, seq, receipt_id) VALUES (?, ?, ?)",
                    (log_id, seq, receipt_id),
                )
                self._conn.commit()
            except BaseException:
                with contextlib.suppress(BaseException):
                    self._conn.rollback()
                raise
        return None

    def count(self) -> int:
        """Return the number of durable AEF identities."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM aef_receipt_index").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> AefDurableReceiptIndex:
        """Return this open index for a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the index when leaving a context manager."""
        self.close()

    @staticmethod
    def _validate_identity(log_id: str, seq: int, receipt_id: str) -> None:
        if not isinstance(log_id, str) or _LOG_ID.fullmatch(log_id) is None:
            raise ValueError("AEF durable index log id must be 64 lowercase hex characters")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("AEF durable index sequence must be a positive integer")
        if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
            raise ValueError("AEF durable index receipt id is malformed")

    @staticmethod
    def _restrict(path: str) -> None:
        if path.startswith(":memory:"):
            return
        with contextlib.suppress(OSError):
            from synapse_channel.core.secure_path import apply_owner_only_file

            apply_owner_only_file(path)
