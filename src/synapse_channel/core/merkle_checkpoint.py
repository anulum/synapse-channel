# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — persisted anti-rollback checkpoint for the durable log
"""Anti-rollback checkpoint for the durable log's Merkle root.

A hash-chained, durable record of the log's Merkle root at a sequence, kept
OUTSIDE the log it attests.

Without a persisted root the "tamper-evident" log attested nothing: truncate
the event store's tail, restart, and the recomputed root simply differs —
silently. The checkpoint chain closes that gap at the local layer:

* on hub startup the log is verified against the latest checkpoint BEFORE
  the hub serves — a log shorter than the checkpoint (tail truncation) or a
  recomputed prefix root that differs from the checkpoint (rewrite) is a
  hard :class:`AntiRollbackError`, not a quiet restart; and
* once verified, the hub appends a fresh checkpoint when the log advanced,
  each one hash-linked to its predecessor, so the checkpoint history is
  itself tamper-evident.

This is the LOCAL anti-rollback layer only. External witnessing (a witness
cosigning checkpoints after verifying consistency) is a separate,
owner-gated design and deliberately out of scope here.

An INTENTIONAL log rewrite — `synapse compact` dropping settled rows, or the
quarantined-row recovery — also trips the detector on the next startup: the
log provably changed, so the hub says so. The operator remedy for a known,
deliberate rewrite is to remove the checkpoint store (owner-only, beside the
log) and let the hub anchor a fresh chain over the re-baselined log. Only
someone with owner access to the host can do that, which is exactly the gap
keeping the store outside the log is meant to close.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.merkle import RunningRoot
from synapse_channel.core.persistence import EventStore

__all__ = [
    "AntiRollbackError",
    "MerkleCheckpoint",
    "MerkleCheckpointStore",
    "checkpoint_path_for",
]


class AntiRollbackError(SynapseError):
    """The durable log failed verification against its persisted checkpoint."""

    code = "anti_rollback"


@dataclass(frozen=True)
class MerkleCheckpoint:
    """One durable attestation of the log's state.

    Attributes
    ----------
    seq : int
        The log sequence this checkpoint attests through (inclusive).
    root : str
        Hex Merkle root over events ``0..seq``.
    created_at : float
        Wall-clock seconds when the checkpoint was written.
    prev_hash : str
        The previous checkpoint's ``checkpoint_hash`` (``""`` for genesis).
    checkpoint_hash : str
        Hex SHA-256 binding ``seq``, ``root``, ``created_at``, and
        ``prev_hash`` together — the chain link.
    """

    seq: int
    root: str
    created_at: float
    prev_hash: str
    checkpoint_hash: str


def checkpoint_path_for(store_path: str | Path) -> Path:
    """Return the default checkpoint database path beside the event store."""
    return Path(f"{store_path}.checkpoint.db")


def _checkpoint_hash(seq: int, root: str, created_at: float, prev_hash: str) -> str:
    """Bind one checkpoint's fields into its chain-link hash."""
    payload = f"{seq}:{root}:{created_at!r}:{prev_hash}".encode()
    return hashlib.sha256(payload).hexdigest()


def _root_through(store: EventStore, through_seq: int | None) -> str:
    """Stream the log into a Merkle root without materialising it."""
    running = RunningRoot()
    for event in store.iter_events(through_seq=through_seq):
        running.add(event)
    return running.commit(through_seq=through_seq).root


class MerkleCheckpointStore:
    """A tiny owner-only SQLite store of hash-chained log checkpoints.

    Parameters
    ----------
    path : pathlib.Path
        Database file. It MUST live outside the event log it attests (a tail
        truncation must not be able to erase the checkpoint with the log).
    clock : Callable, optional
        Wall-clock source; injectable for deterministic tests.
    """

    def __init__(self, path: Path, *, clock: Any = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints ("
            "seq INTEGER PRIMARY KEY,"
            "root TEXT NOT NULL,"
            "created_at REAL NOT NULL,"
            "prev_hash TEXT NOT NULL,"
            "checkpoint_hash TEXT NOT NULL)"
        )
        self._conn.commit()
        self.path.chmod(0o600)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def latest(self) -> MerkleCheckpoint | None:
        """Return the newest checkpoint, or ``None`` on the first run."""
        row = self._conn.execute(
            "SELECT seq, root, created_at, prev_hash, checkpoint_hash "
            "FROM checkpoints ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return MerkleCheckpoint(
            seq=int(row[0]),
            root=str(row[1]),
            created_at=float(row[2]),
            prev_hash=str(row[3]),
            checkpoint_hash=str(row[4]),
        )

    def append(self, seq: int, root: str) -> MerkleCheckpoint:
        """Append a checkpoint, refusing a non-advancing sequence.

        Returns the existing checkpoint when the log has not advanced past
        the latest one (idempotent re-verify on restart).
        """
        latest = self.latest()
        if latest is not None and latest.seq >= seq:
            return latest
        prev_hash = latest.checkpoint_hash if latest is not None else ""
        created = float(self.clock())
        digest = _checkpoint_hash(seq, root, created, prev_hash)
        self._conn.execute(
            "INSERT INTO checkpoints (seq, root, created_at, prev_hash, checkpoint_hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (seq, root, created, prev_hash, digest),
        )
        self._conn.commit()
        return MerkleCheckpoint(
            seq=seq, root=root, created_at=created, prev_hash=prev_hash, checkpoint_hash=digest
        )

    def anchor(self, store: EventStore) -> MerkleCheckpoint:
        """Append a checkpoint attesting the log's current tip.

        Idempotent: a restart whose log did not advance returns the existing
        latest checkpoint rather than chaining a duplicate link.
        """
        return self.append(store.max_seq(), _root_through(store, None))

    def verify(self, store: EventStore) -> None:
        """Verify the event store against the latest checkpoint, fail-closed.

        Raises
        ------
        AntiRollbackError
            When the log is shorter than the checkpoint (tail truncation) or
            the recomputed prefix root differs (rewrite). A first run with no
            checkpoint is clean by construction.
        """
        latest = self.latest()
        if latest is None:
            return
        max_seq = store.max_seq()
        if max_seq < latest.seq:
            raise AntiRollbackError(
                f"durable log is SHORTER than its checkpoint: max_seq {max_seq} "
                f"< checkpoint seq {latest.seq} — tail truncation detected"
            )
        prefix_root = _root_through(store, latest.seq)
        if prefix_root != latest.root:
            raise AntiRollbackError(
                f"durable log prefix does not match its checkpoint: "
                f"recomputed {prefix_root} != checkpoint {latest.root} "
                f"at seq {latest.seq} — log rewrite detected"
            )
