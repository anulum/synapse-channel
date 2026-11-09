# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — journal-backed atomic operations owned by one hub
"""Coordinate keyed commits, duplicate replay and durable receipt settlement.

The owning hub supplies its live journal, state and mutation actor. Replacing
state remains visible at call time; this component creates no actor or store.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from synapse_channel.core.atomic_operations import (
    AtomicExecution,
    OperationDraft,
    OperationRecord,
    canonical_request_digest,
    idempotency_conflict_response,
)
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    parse_protected_write_request,
    protected_write_operation_key,
)
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    recheck_authenticated_protected_request,
)

if TYPE_CHECKING:
    from synapse_channel.core.hub_counters import HubCounters
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.state import SynapseState
    from synapse_channel.core.state_transaction import SerializedStateMutationActor


class AtomicOperationContext(Protocol):
    """Current authoritative objects; bindings are read for each operation."""

    journal: EventStore | None
    state: SynapseState
    state_mutations: SerializedStateMutationActor
    counters: HubCounters


class HubAtomicOperations:
    """Execute and settle requests through the hub's existing sole mutation actor.

    Parameters
    ----------
    context:
        Live bindings owned by the composed hub, without copying state.
    ledger:
        The same resumed idempotency ledger used by ordinary handlers.
    """

    def __init__(self, context: AtomicOperationContext, ledger: HubLedgerGuard) -> None:
        """Retain the current runtime and ledger without acquiring resources."""
        self._context = context
        self._ledger = ledger

    @property
    def ledger(self) -> HubLedgerGuard:
        """Return the existing ledger used by the compatibility facade."""
        return self._ledger

    async def replay_duplicate(
        self,
        msg_type: str,
        data: dict[str, Any],
        websocket: Any,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
    ) -> bool:
        """Replay the cached response for a duplicate mutation, if any.

        Use the existing ledger to replay the original response through this call's
        current socket sender, then settle the same durable receipt.
        """
        outcomes: list[str] = []
        replayed = await self._ledger.maybe_replay_duplicate(
            msg_type,
            data,
            websocket,
            send_json,
            outcomes.append,
        )
        for outcome in outcomes:
            self.record_outcome(outcome)
        if replayed and outcomes == ["replayed"]:
            await self.settle_atomic_operation(data)
        return replayed

    def record_outcome(self, outcome: str) -> None:
        """Increment one bounded, label-free atomic-operation decision counter."""
        if outcome == "inserted":
            self._context.counters.atomic_operations_inserted += 1
            self._context.counters.operation_outbox_pending += 1
        elif outcome == "replayed":
            self._context.counters.atomic_operations_replayed += 1
        elif outcome == "conflict":
            self._context.counters.atomic_operations_conflicts += 1

    async def settle_atomic_operation(self, data: dict[str, Any]) -> None:
        """Mark a committed evidence intent projected after successful transport."""
        if self._context.journal is None:
            return
        operation_key = self._ledger.idempotency_key(data)
        if not operation_key:
            return
        stored = self._context.journal.get_operation(operation_key)
        if stored is None:
            return
        try:
            settled = await asyncio.to_thread(
                self._context.journal.mark_operation_intent_delivered,
                operation_key,
                f"local:{stored.response_sha256}",
            )
        except KeyError:
            return
        # A replayed duplicate settles the same receipt again; count it once.
        if settled:
            self._context.counters.operation_outbox_pending = max(
                0, self._context.counters.operation_outbox_pending - 1
            )

    async def run_atomic_operation(
        self,
        data: dict[str, Any],
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft | None],
        *,
        subject: Any | None = None,
        publish_candidate: Callable[[Any], None] | None = None,
        persist_uncommitted: Callable[[Any], None] | None = None,
        publish: Callable[[Any], None] | None = None,
    ) -> AtomicExecution | None:
        """Run a keyed journal-backed mutation through the atomic operation actor."""
        if self._context.journal is None:
            return None
        operation_key = self._ledger.idempotency_key(data)
        if not operation_key:
            return None
        request_digest = canonical_request_digest(data)
        return await self.run_keyed_atomic_operation(
            operation_key,
            request_digest,
            mutate,
            prepare,
            conflict=lambda existing: idempotency_conflict_response(
                sender=str(data.get("sender") or ""), reference=existing.response
            ),
            subject=subject,
            publish_candidate=publish_candidate,
            persist_uncommitted=persist_uncommitted,
            publish=publish,
            allow_legacy_digestless_replay=True,
            require_committed_response=False,
        )

    async def run_authenticated_protected_write_operation(
        self,
        authenticated: AuthenticatedProtectedRequest,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft],
        *,
        limits: ProtectedWriteProposalLimits,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        current_principal: Callable[[], str],
        clock: Callable[[], float],
        conflict: Callable[[OperationRecord], dict[str, Any]],
    ) -> AtomicExecution:
        """Recheck ingress authority inside the durable mutation actor.

        Parameters
        ----------
        authenticated:
            Server-retained ingress result, never a deserialised client object.
        mutate, prepare:
            Synchronous private-state transition and durable response/event builder.
        limits:
            Explicit enrolled wire limits.
        current_enrollments, current_principal:
            Read the protected registry and authenticated transport principal
            under this actor; the principal is never a claimed sender.
        clock, conflict:
            Fresh trusted server clock and protected conflict response builder.

        Returns
        -------
        AtomicExecution
            Existing journal-backed outcome, not writer execution or settlement.

        Notes
        -----
        Authentication has consumed the replay nonce; callbacks are trusted, synchronous, I/O-free.
        Read-only verbs use separate admission; this installs no handler or OS isolation proof.
        """

        def authorize() -> None:
            recheck_authenticated_protected_request(
                authenticated,
                enrollments=current_enrollments(),
                authenticated_principal=current_principal(),
                now=clock(),
            )

        enrollment = authenticated.enrollment
        return await self.run_protected_write_operation(
            authenticated.parsed.canonical_bytes,
            mutate,
            prepare,
            limits=limits,
            authenticated_principal=enrollment.principal,
            authority_id=enrollment.authority_id,
            authority_continuity=enrollment.authority_continuity,
            conflict=conflict,
            authorize=authorize,
        )

    async def run_protected_write_operation(
        self,
        raw: str | bytes,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft],
        *,
        limits: ProtectedWriteProposalLimits,
        authenticated_principal: str,
        authority_id: str,
        authority_continuity: str,
        conflict: Callable[[OperationRecord], dict[str, Any]],
        authorize: Callable[[], None] | None = None,
    ) -> AtomicExecution:
        """Commit an already-authorised protected mutation through the sole actor.

        This internal boundary is not a wire handler. The caller must authenticate
        and enforce current session, replay, enrollment and operation policy before
        entry. Context parameters must come from server authority, never raw input.
        The mutation callback must recheck current state witnesses under the actor
        lock; admission policy checked before waiting for that lock may be stale.
        Read-only prepare/status do not use this mutation boundary.
        """
        operation_key = protected_write_operation_key(
            raw,
            limits=limits,
            authenticated_principal=authenticated_principal,
            authority_id=authority_id,
            authority_continuity=authority_continuity,
        )
        parsed = parse_protected_write_request(raw, limits=limits)
        if json.loads(parsed.canonical_bytes)["type"] in (
            "protected_write_prepare",
            "protected_write_status",
        ):
            raise ValueError("read-only protected requests cannot use the mutation boundary")
        return await self.run_keyed_atomic_operation(
            operation_key,
            parsed.request_digest,
            mutate,
            prepare,
            conflict=conflict,
            allow_legacy_digestless_replay=False,
            require_committed_response=True,
            authorize=authorize,
        )

    async def run_keyed_atomic_operation(
        self,
        operation_key: str,
        request_digest: str,
        mutate: Callable[[Any], Any],
        prepare: Callable[[Any], OperationDraft | None],
        *,
        conflict: Callable[[OperationRecord], dict[str, Any]],
        allow_legacy_digestless_replay: bool,
        require_committed_response: bool,
        authorize: Callable[[], None] | None = None,
        subject: Any | None = None,
        publish_candidate: Callable[[Any], None] | None = None,
        persist_uncommitted: Callable[[Any], None] | None = None,
        publish: Callable[[Any], None] | None = None,
    ) -> AtomicExecution:
        """Share one journal/cache/actor for legacy and protected operation keys."""
        if self._context.journal is None:
            raise ValueError("atomic execution requires a durable journal")
        journal = self._context.journal

        def commit(draft: OperationDraft) -> Any:
            return journal.commit_operation(
                operation_key=operation_key,
                request_digest=request_digest,
                response=draft.response,
                events=draft.events,
                intent=draft.intent,
                response_event_seq_field=draft.response_event_seq_field,
                finalize_response=draft.finalize_response,
            )

        mutation_subject = self._context.state if subject is None else subject
        candidate_publisher: Callable[[Any], None]
        if subject is None:
            candidate_publisher = self._context.state.publish_from
        elif publish_candidate is not None:
            candidate_publisher = publish_candidate
        else:
            raise ValueError("a non-state atomic subject requires a candidate publisher")

        execution = await self._context.state_mutations.run_atomic(
            mutation_subject,
            mutate,
            request_digest=request_digest,
            lookup=lambda: self._ledger.lookup_operation(operation_key),
            prepare=prepare,
            commit=commit,
            remember=self._ledger.remember_operation,
            conflict=conflict,
            publish_candidate=candidate_publisher,
            persist_uncommitted=persist_uncommitted,
            publish=publish,
            allow_legacy_digestless_replay=allow_legacy_digestless_replay,
            require_committed_response=require_committed_response,
            authorize=authorize,
        )
        self.record_outcome(execution.outcome)
        return execution
