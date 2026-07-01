# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Merkle-tree commitment over the durable event log
"""Commit the durable event log to a Merkle root and prove event inclusion.

The hub's state is a pure fold of an append-only log, so the log itself is the
authoritative record. This module commits that record to a single Merkle root: a
32-byte fingerprint of every event up to a sequence point. Two operators — or two
federated hubs — holding the same log derive the same root, so a mismatch proves
the logs differ, and an **inclusion proof** lets anyone confirm a single event is
in the committed log with ``O(log n)`` sibling hashes, without shipping the whole
log.

The tree follows :rfc:`6962` (Certificate Transparency): leaves are hashed with a
``0x00`` domain-separation prefix and interior nodes with ``0x01``, and an
odd-sized level splits at the largest power of two below its size. That domain
separation is what makes a leaf hash unforgeable as an interior node and closes
the duplicate-leaf ambiguity a naive "duplicate the last node" tree would open.

It complements :mod:`synapse_channel.core.reproduce` (a per-task full-slice
digest) with a log-wide, incrementally provable commitment, and it is read-only
and contacts no live hub. The commitment proves *what the log contains* — integrity
and inclusion — not the semantic correctness of the coordination it records.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.persistence import EventStore, StoredEvent

LEAF_PREFIX = b"\x00"
"""RFC 6962 domain-separation prefix for a leaf hash."""

NODE_PREFIX = b"\x01"
"""RFC 6962 domain-separation prefix for an interior-node hash."""

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()
"""Root of an empty log: the SHA-256 of the empty string, per RFC 6962."""


@dataclass(frozen=True)
class MerkleRoot:
    """A Merkle commitment over the event log up to a sequence point.

    Attributes
    ----------
    root : str
        SHA-256 hex Merkle Tree Hash of the committed events.
    tree_size : int
        Number of events committed.
    first_seq : int
        Sequence of the first committed event, or ``0`` when empty.
    last_seq : int
        Sequence of the last committed event, or ``0`` when empty.
    through_seq : int
        Inclusive sequence ceiling the commitment was built through; ``0`` means
        the whole log was committed.
    """

    root: str
    tree_size: int
    first_seq: int
    last_seq: int
    through_seq: int


@dataclass(frozen=True)
class InclusionProof:
    """An RFC 6962 audit path proving one event is in the committed log.

    Attributes
    ----------
    seq : int
        Sequence of the proven event.
    index : int
        Zero-based leaf index of the event within the committed order.
    tree_size : int
        Size of the tree the proof is against.
    leaf : str
        SHA-256 hex leaf hash of the event.
    path : tuple[str, ...]
        Sibling hashes (hex) from the leaf up to the root.
    root : str
        SHA-256 hex Merkle root the path reconstructs to.
    """

    seq: int
    index: int
    tree_size: int
    leaf: str
    path: tuple[str, ...]
    root: str


def build_root(events: Sequence[StoredEvent], *, through_seq: int | None = None) -> MerkleRoot:
    """Commit the event log to a Merkle root.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.
    through_seq : int or None, optional
        Inclusive sequence ceiling; events after it are excluded. ``None`` commits
        the whole log.

    Returns
    -------
    MerkleRoot
        The commitment over the selected events.
    """
    selected = _selected_events(events, through_seq)
    leaves = [leaf_hash(event) for event in selected]
    return MerkleRoot(
        root=_merkle_tree_hash(leaves).hex(),
        tree_size=len(selected),
        first_seq=selected[0].seq if selected else 0,
        last_seq=selected[-1].seq if selected else 0,
        through_seq=through_seq or 0,
    )


def build_proof(
    events: Sequence[StoredEvent],
    seq: int,
    *,
    through_seq: int | None = None,
) -> InclusionProof | None:
    """Build an inclusion proof for the event at ``seq``.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.
    seq : int
        Sequence of the event to prove.
    through_seq : int or None, optional
        Inclusive sequence ceiling for the tree the proof is against.

    Returns
    -------
    InclusionProof or None
        The audit path, or ``None`` when no committed event has that sequence.
    """
    selected = _selected_events(events, through_seq)
    index = next((i for i, event in enumerate(selected) if event.seq == seq), None)
    if index is None:
        return None
    leaves = [leaf_hash(event) for event in selected]
    path = _audit_path(index, leaves)
    return InclusionProof(
        seq=seq,
        index=index,
        tree_size=len(leaves),
        leaf=leaves[index].hex(),
        path=tuple(node.hex() for node in path),
        root=_merkle_tree_hash(leaves).hex(),
    )


def verify_inclusion(proof: InclusionProof) -> bool:
    """Return whether an inclusion proof reconstructs its own root.

    Recomputes the root from the leaf and audit path alone — no full tree — and
    compares it, in constant time, to the proof's claimed root. A proof whose path
    length does not match its ``(index, tree_size)`` position is rejected.
    """
    if proof.index < 0 or proof.index >= proof.tree_size:
        return False
    if len(proof.path) != _audit_path_length(proof.index, proof.tree_size):
        return False
    try:
        leaf = bytes.fromhex(proof.leaf)
        path = [bytes.fromhex(node) for node in proof.path]
    except ValueError:
        return False
    computed = _root_from_path(proof.index, proof.tree_size, leaf, path)
    return hmac.compare_digest(computed.hex(), proof.root)


def verify_root(root: str, expected: str) -> bool:
    """Return whether a computed root matches an expected root (constant-time)."""
    return hmac.compare_digest(root.strip().lower(), expected.strip().lower())


def leaf_hash(event: StoredEvent) -> bytes:
    """Return the RFC 6962 leaf hash of an event."""
    return hashlib.sha256(LEAF_PREFIX + _canonical_event_bytes(event)).digest()


def run_root(db_path: str | Path, *, through_seq: int | None = None) -> MerkleRoot:
    """Build a Merkle root from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    through_seq : int or None, optional
        Inclusive sequence ceiling.

    Returns
    -------
    MerkleRoot
        The commitment built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return build_root(_load_events(db_path), through_seq=through_seq)


def run_proof(
    db_path: str | Path,
    seq: int,
    *,
    through_seq: int | None = None,
) -> InclusionProof | None:
    """Build an inclusion proof from an existing SQLite event store.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return build_proof(_load_events(db_path), seq, through_seq=through_seq)


def root_to_json(root: MerkleRoot) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a Merkle root."""
    return {
        "root": root.root,
        "tree_size": root.tree_size,
        "first_seq": root.first_seq,
        "last_seq": root.last_seq,
        "through_seq": root.through_seq,
    }


def proof_to_json(proof: InclusionProof) -> dict[str, object]:
    """Return a stable JSON-compatible representation of an inclusion proof."""
    return {
        "seq": proof.seq,
        "index": proof.index,
        "tree_size": proof.tree_size,
        "leaf": proof.leaf,
        "path": list(proof.path),
        "root": proof.root,
    }


def proof_from_json(data: dict[str, object]) -> InclusionProof:
    """Rebuild an inclusion proof from its JSON representation.

    Raises
    ------
    ValueError
        If a required field is missing or has the wrong type.
    """
    try:
        raw_path = data["path"]
        if not isinstance(raw_path, list):
            msg = "proof 'path' must be a list"
            raise TypeError(msg)
        seq = data["seq"]
        index = data["index"]
        tree_size = data["tree_size"]
        if not (isinstance(seq, int) and isinstance(index, int) and isinstance(tree_size, int)):
            msg = "proof 'seq', 'index', and 'tree_size' must be integers"
            raise TypeError(msg)
        return InclusionProof(
            seq=seq,
            index=index,
            tree_size=tree_size,
            leaf=str(data["leaf"]),
            path=tuple(str(node) for node in raw_path),
            root=str(data["root"]),
        )
    except (KeyError, TypeError) as exc:
        msg = f"malformed inclusion proof: {exc}"
        raise ValueError(msg) from exc


def render_root_markdown(root: MerkleRoot) -> str:
    """Render a Merkle root as compact Markdown."""
    if root.tree_size == 0:
        return f"# Merkle root\n\n- Root (sha256): {root.root}\n- Events: 0 (empty log)"
    through = "whole log" if root.through_seq == 0 else f"through seq {root.through_seq}"
    return "\n".join(
        [
            "# Merkle root",
            "",
            f"- Root (sha256): {root.root}",
            f"- Events: {root.tree_size} ({through})",
            f"- Sequence range: {root.first_seq}..{root.last_seq}",
        ]
    )


def render_proof_markdown(proof: InclusionProof) -> str:
    """Render an inclusion proof as compact Markdown."""
    lines = [
        f"# Inclusion proof: seq {proof.seq}",
        "",
        f"- Root (sha256): {proof.root}",
        f"- Leaf (sha256): {proof.leaf}",
        f"- Position: index {proof.index} of tree size {proof.tree_size}",
        f"- Audit path ({len(proof.path)} hashes):",
    ]
    if proof.path:
        lines.extend(f"  - {node}" for node in proof.path)
    else:
        lines.append("  - (none — single-event log)")
    return "\n".join(lines)


def _selected_events(
    events: Sequence[StoredEvent],
    through_seq: int | None,
) -> list[StoredEvent]:
    """Return events at or before ``through_seq``, ordered by sequence."""
    ordered = sorted(events, key=lambda event: event.seq)
    if through_seq is None:
        return ordered
    return [event for event in ordered if event.seq <= through_seq]


def _canonical_event_bytes(event: StoredEvent) -> bytes:
    """Return the stable canonical encoding of one event.

    Sequence, timestamp, kind, and payload are encoded as key-sorted, separator-
    tight JSON so the same event serialises to the same bytes on every machine.
    """
    canonical = {"seq": event.seq, "ts": event.ts, "kind": event.kind, "payload": event.payload}
    return json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _node_hash(left: bytes, right: bytes) -> bytes:
    """Return the RFC 6962 interior-node hash of two child hashes."""
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _largest_power_of_two_below(size: int) -> int:
    """Return the largest power of two strictly less than ``size`` (``size >= 2``)."""
    return 1 << ((size - 1).bit_length() - 1)


def _merkle_tree_hash(leaves: Sequence[bytes]) -> bytes:
    """Return the RFC 6962 Merkle Tree Hash of a list of leaf hashes."""
    size = len(leaves)
    if size == 0:
        return hashlib.sha256(b"").digest()
    if size == 1:
        return leaves[0]
    split = _largest_power_of_two_below(size)
    return _node_hash(_merkle_tree_hash(leaves[:split]), _merkle_tree_hash(leaves[split:]))


def _audit_path(index: int, leaves: Sequence[bytes]) -> list[bytes]:
    """Return the RFC 6962 audit path for ``index`` within ``leaves``."""
    size = len(leaves)
    if size <= 1:
        return []
    split = _largest_power_of_two_below(size)
    if index < split:
        return [*_audit_path(index, leaves[:split]), _merkle_tree_hash(leaves[split:])]
    return [*_audit_path(index - split, leaves[split:]), _merkle_tree_hash(leaves[:split])]


def _audit_path_length(index: int, size: int) -> int:
    """Return the expected audit-path length for ``index`` in a tree of ``size``."""
    if size <= 1:
        return 0
    split = _largest_power_of_two_below(size)
    if index < split:
        return _audit_path_length(index, split) + 1
    return _audit_path_length(index - split, size - split) + 1


def _root_from_path(index: int, size: int, leaf: bytes, path: Sequence[bytes]) -> bytes:
    """Reconstruct a Merkle root from a leaf and its audit path."""
    if size == 1:
        return leaf
    split = _largest_power_of_two_below(size)
    if index < split:
        return _node_hash(_root_from_path(index, split, leaf, path[:-1]), path[-1])
    return _node_hash(path[-1], _root_from_path(index - split, size - split, leaf, path[:-1]))


def _load_events(db_path: str | Path) -> tuple[StoredEvent, ...]:
    """Load all events from an event store, raising if it is missing."""
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        return tuple(store.read_all())
    finally:
        store.close()
