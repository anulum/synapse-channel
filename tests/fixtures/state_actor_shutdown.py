# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real event-loop shutdown during a contended claim append
"""Commit a real claim across normal runner cancellation and native SQLite contention."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, cast

from synapse_channel.core.atomic_operations import (
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
    idempotency_conflict_response,
)
from synapse_channel.core.idempotency import IdempotencyCache
from synapse_channel.core.journal import record_claim, replay
from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.state import SynapseState, TaskClaim
from synapse_channel.core.state_transaction import SerializedStateMutationActor

Mode = Literal["plain", "uncommitted", "atomic"]


class ShutdownClaimScenario:
    """Own one real journal, contending connection and the pending public mutation."""

    def __init__(self, root: Path, mode: Mode, *, publish_updates: bool = True) -> None:
        """Hold the actual SQLite writer lock before starting the actor operation."""
        root.mkdir(mode=0o700)
        self.store = EventStore(root / "authority.db")
        self.blocker = sqlite3.connect(
            root / "authority.db", isolation_level=None, check_same_thread=False
        )
        self.blocker.execute("BEGIN IMMEDIATE")
        self.state = SynapseState()
        self.actor = SerializedStateMutationActor()
        self.cache = IdempotencyCache()
        self.mode = mode
        self.publish_updates = publish_updates
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.request_context = ContextVar("request_principal", default="missing")
        self.observed_contexts: list[str] = []
        self.publications: list[dict[str, object]] = []
        self.task: asyncio.Task[None] | None = None
        self.releaser: threading.Thread | None = None
        self.digest = canonical_request_digest(
            {"sender": "EXAMPLE/author", "type": "claim", "task_id": "shutdown-claim"}
        )

    def mutate(self, candidate: SynapseState) -> TaskClaim:
        """Request a real scoped-state claim without publishing it prematurely."""
        accepted, reason = candidate.claim("EXAMPLE/author", "shutdown-claim", ttl_seconds=60)
        assert accepted, reason
        return candidate.claims["shutdown-claim"]

    def append(self, claim: TaskClaim) -> None:
        """Enter actual journal recording while the independent writer lock is held."""
        self.observed_contexts.append(self.request_context.get())
        self.entered.set()
        record_claim(self.store, claim)

    def prepare(self, claim: TaskClaim) -> OperationDraft | None:
        """Use the real claim row and exact response for the keyed commit path."""
        if self.mode == "uncommitted":
            return None
        return OperationDraft(
            response={"type": "claim_granted", "task_id": claim.task_id, "epoch": claim.epoch},
            events=(("claim", claim.as_persisted_dict()),),
            intent={"task_id": claim.task_id},
        )

    def commit(self, draft: OperationDraft) -> OperationCommitResult:
        """Block the public atomic commit on the same native SQLite transaction."""
        self.observed_contexts.append(self.request_context.get())
        self.entered.set()
        return self.store.commit_operation(
            operation_key="shutdown-key",
            request_digest=self.digest,
            response=draft.response,
            events=draft.events,
            intent=draft.intent,
        )

    def lookup(self) -> OperationRecord | None:
        """Consult the actual journal rather than substituting an empty lookup."""
        operation = self.store.get_operation("shutdown-key")
        if operation is None:
            return None
        return OperationRecord(
            operation.operation_key, operation.request_digest, operation.response
        )

    def remember(self, record: OperationRecord) -> None:
        """Publish the exact committed response into the production replay cache."""
        self.cache.put(record.key, record.response, request_digest=record.request_digest)

    def publish(self, claim: TaskClaim) -> None:
        """Observe the committed journal and published live state together."""
        assert self.state.claims[claim.task_id].as_persisted_dict() == claim.as_persisted_dict()
        assert replay(self.store).state.claims[claim.task_id].as_persisted_dict() == (
            claim.as_persisted_dict()
        )
        self.publications.append(claim.as_persisted_dict())

    async def apply(self) -> None:
        """Run one of the three public actor persistence paths."""
        publish = self.publish if self.publish_updates else None
        if self.mode == "plain":
            await self.actor.run(self.state, self.mutate, persist=self.append, publish=publish)
            return
        await self.actor.run_atomic(
            self.state,
            self.mutate,
            request_digest=self.digest,
            lookup=self.lookup,
            prepare=self.prepare,
            commit=self.commit,
            remember=self.remember,
            conflict=lambda _record: idempotency_conflict_response(sender="EXAMPLE/author"),
            persist_uncommitted=self.append,
            publish=publish,
        )

    def release(self) -> None:
        """Release contention after premature actor completion or a bounded wait."""
        self.finished.wait(0.25)
        self.blocker.commit()

    async def start(self) -> None:
        """Return with an actual append in flight so asyncio.run cancels pending tasks."""
        self.request_context.set("EXAMPLE/author")
        self.task = asyncio.create_task(self.apply())
        self.task.add_done_callback(lambda _task: self.finished.set())
        deadline = time.monotonic() + 5
        while not self.entered.is_set():
            assert time.monotonic() < deadline, "the persistence callback did not start"
            await asyncio.sleep(0.001)
        self.releaser = threading.Thread(target=self.release)
        self.releaser.start()

    def run(self) -> dict[str, object]:
        """Observe journal, replay cache and live state after normal runner shutdown."""
        asyncio.run(self.start())
        assert self.task is not None and self.releaser is not None
        self.releaser.join(5)
        assert not self.releaser.is_alive()
        return {
            "journal_claim_count": sum(event.kind == "claim" for event in self.store.read_all()),
            "live_claim_present": "shutdown-claim" in self.state.claims,
            "restored_claim_present": "shutdown-claim" in replay(self.store).state.claims,
            "actor_cancelled": self.task.cancelled(),
            "response_remembered": self.cache.get("shutdown-key") is not None,
            "stored_operations": len(self.store.read_operations()),
            "worker_contexts": self.observed_contexts,
            "publications": self.publications,
        }

    def close(self) -> None:
        """Close both real connections after their writer thread has drained."""
        self.blocker.close()
        self.store.close()


def main() -> None:
    """Run only a declared scenario in a fresh caller-owned directory."""
    root, mode = Path(sys.argv[1]), sys.argv[2]
    if mode not in {"plain", "uncommitted", "atomic"}:
        raise ValueError("unknown shutdown scenario")
    scenario = ShutdownClaimScenario(root, cast(Mode, mode))
    try:
        print(json.dumps(scenario.run(), sort_keys=True))
    finally:
        scenario.close()


if __name__ == "__main__":
    main()
