# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded current-owner work lookup
"""Maintain recipient/project seek indexes and reversible mutation revisions."""

from __future__ import annotations

import hashlib
import json
import secrets
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass, field
from typing import Literal

WorkKind = Literal["tasks", "claims"]
WorkScope = tuple[str, str | None]


@dataclass(frozen=True)
class OwnedEntry:
    """Bind one native work record to an owner and a fresh mutation generation."""

    task_id: str
    owner: str
    project: str
    kind: WorkKind
    key: str
    version: str

    def metadata(self) -> dict[str, str]:
        """Expose only a kind, opaque seek key and mutation generation."""
        return {"kind": self.kind, "key": self.key, "item_version": self.version}


@dataclass
class _Bucket:
    """Keep seek order and a reversible signature without copying record bodies."""

    keys: list[str] = field(default_factory=list)
    records: dict[str, OwnedEntry] = field(default_factory=dict)
    signature: int = 0

    def put(self, entry: OwnedEntry) -> None:
        """Replace one generation without changing the seek order on body updates."""
        previous = self.records.get(entry.key)
        if previous is None:
            insort(self.keys, entry.key)
        else:
            self.signature ^= int(previous.version, 16)
        self.records[entry.key] = entry
        self.signature ^= int(entry.version, 16)

    def remove(self, entry: OwnedEntry) -> None:
        """Reverse one entry's contribution, including during transaction rollback."""
        del self.records[entry.key]
        self.keys.pop(bisect_left(self.keys, entry.key))
        self.signature ^= int(entry.version, 16)


class OwnedWorkIndex:
    """Seek current assigned work without sorting or scanning a global registry.

    Parameters
    ----------
    kind : {"tasks", "claims"}
        Native record family. Each entry belongs to both its exact owner's
        all-work bucket and its owner's explicit project bucket.
    """

    def __init__(self, kind: WorkKind) -> None:
        """Bind one family and a fresh boot fence, with no historical tombstones."""
        if kind not in ("tasks", "claims"):
            raise ValueError("invalid owned work family")
        self.kind = kind
        self.boot = secrets.token_hex(32)
        self.entries: dict[str, OwnedEntry] = {}
        self.buckets: dict[WorkScope, _Bucket] = {}

    def revision(self, owner: str, project: str | None) -> str:
        """Fence only this recipient's scope and invalidate every pre-restart cursor."""
        bucket = self.buckets.get((owner, project))
        material = [
            self.boot,
            owner,
            project,
            len(bucket.keys) if bucket else 0,
            bucket.signature if bucket else 0,
        ]
        return hashlib.sha256(json.dumps(material).encode()).hexdigest()

    def remove(self, task_id: str) -> None:
        """Remove one owner entry and both empty buckets without retaining tombstones."""
        entry = self.entries.pop(task_id, None)
        if entry is None:
            return
        for scope in ((entry.owner, None), (entry.owner, entry.project)):
            bucket = self.buckets[scope]
            bucket.remove(entry)
            if not bucket.keys:
                del self.buckets[scope]

    def restore(self, task_id: str, entry: OwnedEntry | None) -> None:
        """Restore one exact entry and recipient revisions after a failed mutation.

        Parameters
        ----------
        task_id : str
            Native record whose provisional changes are being undone.
        entry : OwnedEntry or None
            Previously captured immutable generation, or absence. The reversible
            signature restores affected recipient revisions without a global copy.
        """
        previous = self.entries.get(task_id)
        if entry is None:
            self.remove(task_id)
            return
        if entry.task_id != task_id or entry.kind != self.kind:
            raise ValueError("owned work restore identity changed")
        if previous is not None and (previous.owner, previous.project) != (
            entry.owner,
            entry.project,
        ):
            self.remove(task_id)
        self.entries[task_id] = entry
        for scope in ((entry.owner, None), (entry.owner, entry.project)):
            self.buckets.setdefault(scope, _Bucket()).put(entry)

    def set(self, task_id: str, owner: str, project: str) -> None:
        """Publish a fresh body generation, moving membership only when necessary."""
        if not owner:
            self.remove(task_id)
            return
        key = hashlib.sha256(json.dumps([self.kind, task_id]).encode()).hexdigest()
        self.restore(
            task_id, OwnedEntry(task_id, owner, project, self.kind, key, secrets.token_hex(32))
        )

    def get(self, owner: str, project: str | None, key: str) -> OwnedEntry | None:
        """Resolve a key inside exactly one owner/project scope."""
        bucket = self.buckets.get((owner, project))
        return bucket.records.get(key) if bucket else None

    def page(
        self, owner: str, project: str | None, *, after_key: str, limit: int
    ) -> tuple[OwnedEntry, ...]:
        """Read at most limit ordered entries after a seek key in logarithmic time."""
        bucket = self.buckets.get((owner, project))
        if bucket is None:
            return ()
        start = bisect_right(bucket.keys, after_key)
        return tuple(bucket.records[key] for key in bucket.keys[start : start + limit])
