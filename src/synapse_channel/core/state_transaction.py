# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — rollback boundary for durable state mutations
"""Make an in-memory claim mutation atomic with its durable journal append.

The constant-cost boundary snapshots one task before its normal state method.
If the following append raises, the helper restores that task, its epoch and
checkpoint accounting, and any explicitly named presence entries. Independent
heartbeat housekeeping remains valid; lease indexing is rebuilt only on the
exceptional path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import copy_context
from copy import copy, deepcopy
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, TypeVar, cast

from synapse_channel.core.atomic_operations import (
    AtomicExecution,
    OperationDraft,
    OperationRecord,
)

if TYPE_CHECKING:
    from synapse_channel.core.persistence import OperationCommitResult
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_models import TaskClaim

MutationResult = TypeVar("MutationResult")
MutationSubject = TypeVar("MutationSubject")


def _submit_append(
    persist: Callable[[MutationResult], MutationSubject], result: MutationResult
) -> asyncio.Future[MutationSubject]:
    """Retain a context-aware executor future outside runner task cancellation.

    asyncio.run cancels every pending Task during shutdown. The executor Future
    must remain independently awaitable until the actual native append finishes;
    cancelling a to_thread Task cannot stop its already running storage thread.
    """
    context = copy_context()
    return asyncio.get_running_loop().run_in_executor(None, lambda: context.run(persist, result))


async def _drain_append(
    append: asyncio.Future[MutationResult],
) -> tuple[MutationResult, bool]:
    """Drain the authoritative worker result before propagating caller cancellation."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(append), cancelled
        except asyncio.CancelledError:
            if append.done():
                # A worker may itself raise CancelledError. Preserve that failure
                # rather than spinning or publishing an uncommitted candidate.
                append.result()
            cancelled = True


@dataclass(frozen=True)
class _TaskStateSnapshot:
    """State needed to undo one synchronous claim-family mutation."""

    task_id: str
    claim: tuple[TaskClaim, TaskClaim] | None
    checkpoint_present: bool
    checkpoint: str
    presence: tuple[tuple[str, bool, float], ...]
    epoch_seq: int

    @classmethod
    def capture(
        cls,
        state: SynapseState,
        task_id: str,
        presence_agents: Sequence[str],
    ) -> _TaskStateSnapshot:
        """Capture one task plus presence entries the mutation may update."""
        claim = state.claims.get(task_id)
        checkpoint_present = task_id in state.expired_checkpoints
        return cls(
            task_id=task_id,
            claim=(claim, copy(claim)) if claim is not None else None,
            checkpoint_present=checkpoint_present,
            checkpoint=state.expired_checkpoints.get(task_id, ""),
            presence=tuple(
                (agent, agent in state.last_seen, state.last_seen.get(agent, 0.0))
                for agent in dict.fromkeys(presence_agents)
            ),
            epoch_seq=state._epoch_seq,
        )

    def restore(self, state: SynapseState) -> None:
        """Restore the task and accounting captured before the mutation."""
        checkpoint = self.checkpoint if self.checkpoint_present else None
        if self.claim is None:
            state.claims.pop(self.task_id, None)
        else:
            claim, value = self.claim
            for field in fields(value):
                setattr(claim, field.name, getattr(value, field.name))
            if value.lease_expires_at <= time.time():
                state.claims.pop(self.task_id, None)
                checkpoint = value.checkpoint or checkpoint
            else:
                state.claims[self.task_id] = claim

        if checkpoint is not None:
            state.expired_checkpoints[self.task_id] = checkpoint
        else:
            state.expired_checkpoints.pop(self.task_id, None)

        for agent, present, timestamp in self.presence:
            if present:
                state.last_seen[agent] = timestamp
            else:
                state.last_seen.pop(agent, None)

        state._epoch_seq = self.epoch_seq
        state.reindex_leases()


@contextmanager
def durable_state_transaction(
    state: SynapseState,
    task_id: str,
    *,
    enabled: bool,
    restore_presence: Sequence[str] = (),
) -> Iterator[None]:
    """Roll back one task if its synchronous durable append raises.

    The context may contain state mutation plus synchronous journal I/O only;
    it must not contain an event-loop yield. Rollback restores the captured
    claim object and its fields. A lease that expires during I/O stays expired;
    only its previously captured checkpoint remains available to a successor.

    Parameters
    ----------
    state : SynapseState
        Live state owning the task and its accounting.
    task_id : str
        Exact task whose provisional mutation may need restoring.
    enabled : bool
        Capture and restore state when a durable journal is configured.
    restore_presence : Sequence[str], optional
        Agents whose presence entries belong to this mutation.

    Yields
    ------
    None
        A synchronous mutation and journal-append boundary.
    """
    snapshot = _TaskStateSnapshot.capture(state, task_id, restore_presence) if enabled else None
    try:
        yield
    except BaseException:
        if snapshot is not None:
            snapshot.restore(state)
        raise


class SerializedStateMutationActor:
    """Serialize durable state transitions without blocking the event loop.

    A durable transition is first applied to a private deep copy.  Its journal
    append then runs in a worker thread while the live state remains unchanged.
    Only a successful append publishes the candidate, synchronously and without
    yielding.  The actor lock orders concurrent state mutations and prevents a
    heartbeat or another transition from being lost while one append is in
    flight.

    Non-durable hubs mutate the live state directly under the same lock.  This
    keeps one ordering model without paying the copy cost when there is no I/O
    boundary to cross.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def run(
        self,
        state: SynapseState,
        mutate: Callable[[SynapseState], MutationResult],
        *,
        persist: Callable[[MutationResult], None] | None = None,
        publish: Callable[[MutationResult], None] | None = None,
    ) -> MutationResult:
        """Apply one serialized mutation and publish it only after persistence."""
        return await self.run_subject(
            state,
            mutate,
            persist=persist,
            publish_candidate=state.publish_from,
            publish=publish,
        )

    async def run_subject(
        self,
        subject: MutationSubject,
        mutate: Callable[[MutationSubject], MutationResult],
        *,
        persist: Callable[[MutationResult], None] | None = None,
        publish_candidate: Callable[[MutationSubject], None],
        publish: Callable[[MutationResult], None] | None = None,
    ) -> MutationResult:
        """Serialize and durably publish a mutation of any copyable state subject.

        Repeated caller cancellation cannot release the actor lock or abandon
        an in-flight journal thread. Its outcome is drained before publication
        and cancellation propagation; a failed append never publishes state.

        Parameters
        ----------
        subject : MutationSubject
            Copyable state mutated under this actor's serial ordering.
        mutate : Callable
            Synchronous transition applied to the live volatile subject or a
            private durable candidate.
        persist : Callable or None, optional
            Journal append run in an executor with the caller's context.
            It must return successfully only after its durable write completes.
        publish_candidate : Callable
            Synchronously install the successfully persisted candidate.
        publish : Callable or None, optional
            Observe the result after live-state publication.

        Returns
        -------
        MutationResult
            The transition result after successful publication.

        Raises
        ------
        asyncio.CancelledError
            After draining a caller-cancelled append and publishing its success,
            or immediately when the persistence callback itself is cancelled.
        Exception
            The mutation or persistence failure; a failed durable append does
            not publish its private candidate.
        """
        async with self._lock:
            if persist is None:
                result = mutate(subject)
                if publish is not None:
                    publish(result)
                return result

            candidate = deepcopy(subject)
            result = mutate(candidate)
            _, cancelled = await _drain_append(_submit_append(persist, result))
            publish_candidate(candidate)
            if publish is not None:
                publish(result)
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def run_atomic(
        self,
        subject: MutationSubject,
        mutate: Callable[[MutationSubject], MutationResult],
        *,
        request_digest: str,
        lookup: Callable[[], OperationRecord | None],
        prepare: Callable[[MutationResult], OperationDraft | None],
        commit: Callable[[OperationDraft], OperationCommitResult],
        remember: Callable[[OperationRecord], None],
        conflict: Callable[[OperationRecord], dict[str, Any]],
        publish_candidate: Callable[[MutationSubject], None] | None = None,
        persist_uncommitted: Callable[[MutationResult], None] | None = None,
        publish: Callable[[MutationResult], None] | None = None,
        stage_hook: Callable[[str], None] | None = None,
        allow_legacy_digestless_replay: bool = False,
        require_committed_response: bool = False,
        authorize: Callable[[], None] | None = None,
    ) -> AtomicExecution:
        """Apply one keyed mutation with a second lookup inside the actor lock.

        Digest-less historical records conflict unless a legacy caller explicitly
        enables compatibility. Protected authority operations must keep the
        default: an unbound historical response cannot prove request equality.
        A durable-only caller must require a committed response; a missing draft
        then fails before the candidate or any uncommitted path can be published.
        When supplied, authorization runs under the lock before replay/conflict
        lookup as well as mutation. It must be synchronous and perform no I/O.

        Notes
        -----
        Both keyed and uncommitted journal appends retain their executor Future
        across repeated caller cancellation and normal event-loop shutdown.
        The actor drains the authoritative outcome before releasing its lock;
        an inserted operation is remembered and published before cancellation
        propagates. A persistence failure leaves the private candidate unpublished.
        """
        hook = stage_hook or (lambda _stage: None)
        candidate_publisher: Callable[[MutationSubject], None]
        if publish_candidate is None:
            candidate_publisher = cast("Any", subject).publish_from
        else:
            candidate_publisher = publish_candidate
        async with self._lock:
            if authorize is not None:
                authorize()
            existing = lookup()
            if existing is not None:
                if existing.request_digest == request_digest or (
                    existing.request_digest is None and allow_legacy_digestless_replay
                ):
                    return AtomicExecution("replayed", None, existing.response)
                return AtomicExecution("conflict", None, conflict(existing))

            hook("before_candidate_mutation")
            candidate = deepcopy(subject)
            result = mutate(candidate)
            draft = prepare(result)
            hook("after_candidate_response")
            if draft is None:
                if require_committed_response:
                    raise ValueError("this operation requires a durable response draft")
                cancelled = False
                if persist_uncommitted is not None:
                    _, cancelled = await _drain_append(_submit_append(persist_uncommitted, result))
                candidate_publisher(candidate)
                if publish is not None:
                    publish(result)
                if cancelled:
                    raise asyncio.CancelledError
                return AtomicExecution("uncommitted", result, None)

            committed, cancelled = await _drain_append(_submit_append(commit, draft))

            stored = OperationRecord(
                key=committed.operation.operation_key,
                request_digest=committed.operation.request_digest,
                response=committed.operation.response,
            )
            remember(stored)
            if committed.outcome == "inserted":
                candidate_publisher(candidate)
                if publish is not None:
                    publish(result)
                hook("after_publication")
                execution = AtomicExecution("inserted", result, stored.response)
            elif committed.outcome == "replayed":
                execution = AtomicExecution("replayed", None, stored.response)
            else:
                execution = AtomicExecution("conflict", None, conflict(stored))
            if cancelled:
                raise asyncio.CancelledError
            return execution
