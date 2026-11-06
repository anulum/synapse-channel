# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the hub journal as the oracle of an interop journey
"""Check what an interop journey did against the hub's own journal.

A client can report success while the hub recorded nothing, so a journey is
judged by the durable store, read after the hub has stopped. Every assertion
here is positive: it names a record that must exist and returns its sequence.
A store that holds nothing therefore fails on the first assertion, never
passes by default. The rows each assertion matched are kept, so the record
can carry their digest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


class OracleFailure(AssertionError):
    """A journal assertion failed; ``step`` names it."""

    def __init__(self, step: str, detail: str) -> None:
        """Keep the failed step's name apart from its detail."""
        super().__init__(f"{step}: {detail}")
        self.step = step


@dataclass
class JournalOracle:
    """The events of one stopped hub's store, and the rows asserted so far."""

    events: list[StoredEvent]
    asserted: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def read(cls, db: Path) -> JournalOracle:
        """Read every event of the store at ``db``, which must already exist."""
        if not db.is_file():
            raise OracleFailure("journal-read", f"no store at {db.name}")
        store = EventStore(db)
        try:
            return cls(events=list(store.iter_events()))
        finally:
            store.close()

    def _keep(self, event: StoredEvent) -> int:
        self.asserted.append({"seq": event.seq, "kind": event.kind, "payload": event.payload})
        return event.seq

    def claim_granted(self, task_id: str, owner: str) -> int:
        """Return the sequence of the first claim of ``task_id`` granted to ``owner``."""
        for event in self.events:
            payload = event.payload
            if (
                event.kind == EventKind.CLAIM
                and payload.get("task_id") == task_id
                and payload.get("owner") == owner
            ):
                return self._keep(event)
        raise OracleFailure("journal-claim-granted", f"no claim of the task granted to {owner}")

    def claim_denied(self, task_id: str, claimant: str, *, after: int) -> int:
        """Return the sequence of a live-lease denial of ``task_id`` to ``claimant``.

        The digest of the task id is computed here from ``task_id``, never
        copied from the journal.
        """
        task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        for event in self.events:
            payload = event.payload
            if (
                event.seq > after
                and event.kind == EventKind.CLAIM_DENIAL
                and payload.get("claimant") == claimant
                and payload.get("reason_code") == "LEASE_LIVE"
                and payload.get("task_id_sha256") == task_digest
            ):
                return self._keep(event)
        raise OracleFailure(
            "journal-claim-denied", f"no live-lease denial of the task to {claimant}"
        )

    def no_release_between(self, task_id: str, first: int, last: int) -> None:
        """Require that ``task_id`` was not released between two sequences."""
        for event in self.events:
            if (
                first < event.seq < last
                and event.kind == EventKind.RELEASE
                and event.payload.get("task_id") == task_id
            ):
                raise OracleFailure(
                    "journal-no-release-between", f"the task was released at seq {event.seq}"
                )

    def released(self, task_id: str, *, after: int) -> int:
        """Return the sequence of the release of ``task_id`` after ``after``."""
        for event in self.events:
            if (
                event.seq > after
                and event.kind == EventKind.RELEASE
                and event.payload.get("task_id") == task_id
            ):
                return self._keep(event)
        raise OracleFailure("journal-released", "no release of the task")

    def chat_recorded(
        self, sender: str, target: str, text: str, *, a2a_task_id: str | None = None
    ) -> int:
        """Return the sequence of a chat from ``sender`` to ``target`` carrying ``text``.

        With ``a2a_task_id`` the chat must also carry that A2A task id in its
        metadata, which ties the forwarded message to the client's task.
        """
        for event in self.events:
            payload = event.payload
            metadata = payload.get("metadata")
            if (
                event.kind == EventKind.CHAT
                and payload.get("sender") == sender
                and payload.get("target") == target
                and payload.get("payload") == text
                and (
                    a2a_task_id is None
                    or (isinstance(metadata, dict) and metadata.get("a2aTaskId") == a2a_task_id)
                )
            ):
                return self._keep(event)
        raise OracleFailure("journal-chat", f"no chat from {sender} to {target}")
