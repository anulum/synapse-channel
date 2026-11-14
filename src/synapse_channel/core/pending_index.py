# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — recipient index for historical pending assignments
"""Look up one recipient's retained work without scanning the global board."""

from __future__ import annotations

import hashlib
import json


def assignment_key(kind: str, task_id: str, owner: str, anchor: int) -> str:
    """Fingerprint one assignment generation without exposing task identifiers."""
    return hashlib.sha256(json.dumps([kind, task_id, owner, anchor]).encode()).hexdigest()


def scoped_owner(task_id: str, owner: str, project: str) -> bool:
    """Require a project seat, using the task's own project when the record has one.

    A non-empty ``project`` is that field and is the scope: the owner must be
    ``project/seat``. The task name may use another prefix; the field wins.

    An empty ``project`` means the record carries no project. The name-prefix
    rule remains: the owner is ``prefix/seat`` and the task name begins with
    ``prefix/``.
    """
    prefix, separator, suffix = owner.partition("/")
    if not (separator and prefix and suffix and len(owner) <= 256 and owner.strip() == owner):
        return False
    if project:
        return prefix == project
    return task_id.startswith(prefix + "/")


class PendingRecipientIndex:
    """Maintain reverse and recipient indexes as part of one owning projection."""

    def __init__(self) -> None:
        """Start with no retained assignment or recipient bucket."""
        self.entries: dict[str, tuple[str, str]] = {}
        self.recipients: dict[str, dict[str, str]] = {}

    def remove(self, task_id: str) -> None:
        """Remove an assignment and empty bucket in constant expected time."""
        previous = self.entries.pop(task_id, None)
        if previous is not None:
            bucket = self.recipients[previous[0]]
            del bucket[task_id]
            if not bucket:
                del self.recipients[previous[0]]

    def set(self, task_id: str, owner: str, key: str) -> None:
        """Install one assignment, retiring its previous recipient atomically."""
        if self.entries.get(task_id) == (owner, key):
            return
        self.remove(task_id)
        self.entries[task_id] = owner, key
        self.recipients.setdefault(owner, {})[task_id] = key

    def retain(self, task_id: str, owner: str, key: str) -> None:
        """Keep a continuing historical assignment; never infer a new one."""
        if self.entries.get(task_id) != (owner, key):
            self.remove(task_id)

    def rows(self, owner: str, *, limit: int) -> tuple[tuple[str, str], ...]:
        """Read a complete bounded recipient bucket, refusing partial success."""
        bucket = self.recipients.get(owner, {})
        if len(bucket) > limit:
            raise ValueError("pending assignment capacity exceeded")
        return tuple(sorted(bucket.items()))
