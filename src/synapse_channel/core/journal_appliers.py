# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — typed journal reconstruction by event family
"""Apply retained journal facts without re-running live admission algorithms."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.ledger import Blackboard, LedgerTask, ProgressNote
from synapse_channel.core.operator_relay_approval import RelayApprovalLedger
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.state import ResourceOffer, SynapseState, TaskClaim

if TYPE_CHECKING:
    from synapse_channel.core.journal import EventKind, UnsupportedProtectedWriteHistoryError


@dataclass
class ReplayContext:
    """Mutable projections accumulated in journal sequence order.

    Attributes
    ----------
    state, blackboard : SynapseState, Blackboard
        Coordination and plan projections with their caller-selected bounds.
    chat_history, idempotency : list, OrderedDict
        Chat facts and latest responses ordered by their most recent event.
    finding_counts_by_actor, relay_approvals : dict, RelayApprovalLedger
        Retained provenance counters and validated relay approval transitions.
    corrupt_rows : list of CorruptEventRow
        Safe markers that keep incomplete reconstruction observable.
    message_seq, epoch_seq : int
        Greatest retained chat message and claim epoch identifiers.
    """

    state: SynapseState
    blackboard: Blackboard
    chat_history: list[dict[str, Any]]
    idempotency: OrderedDict[str, dict[str, Any]]
    finding_counts_by_actor: dict[str, int]
    relay_approvals: RelayApprovalLedger
    corrupt_rows: list[CorruptEventRow]
    message_seq: int = 0
    epoch_seq: int = 0


@dataclass(frozen=True, kw_only=True)
class ReplayPorts:
    """Deferred decoders and custody checks retained by the journal owner.

    Attributes
    ----------
    claim, ledger_task, progress : callable
        Existing persisted-snapshot decoders, looked up at their call sites.
    protected_task_held, protected_scope_held : callable
        Existing task and complete claim-scope custody predicates.
    scope_checked_kinds : frozenset of str
        Claim mutations that must also check protected scopes.
    protected_restorers : mapping of str to callable
        Explicitly supported protected-history reconstruction callbacks.
    unsupported : callable
        Existing typed refusal constructor for unsupported or conflicting custody.
    """

    claim: Callable[[dict[str, Any]], TaskClaim]
    ledger_task: Callable[[dict[str, Any]], LedgerTask]
    progress: Callable[[dict[str, Any]], ProgressNote]
    protected_task_held: Callable[[str], bool]
    protected_scope_held: Callable[[TaskClaim], bool]
    scope_checked_kinds: frozenset[str]
    protected_restorers: Mapping[str, Callable[[StoredEvent], None]]
    unsupported: Callable[[str], UnsupportedProtectedWriteHistoryError]


ReplayApplier = Callable[[ReplayContext, ReplayPorts, StoredEvent], None]
"""Apply one known event to its owning projections."""


def _claim(context: ReplayContext, ports: ReplayPorts, event: StoredEvent) -> None:
    claim = ports.claim(event.payload)
    if ports.protected_task_held(claim.task_id) or (
        event.kind in ports.scope_checked_kinds and ports.protected_scope_held(claim)
    ):
        raise ports.unsupported(
            f"legacy claim mutation conflicts with protected custody at sequence {event.seq}"
        )
    context.state.claims[claim.task_id] = claim
    context.state.last_seen[claim.owner] = claim.claimed_at
    context.epoch_seq = max(context.epoch_seq, claim.epoch)


def _release(context: ReplayContext, ports: ReplayPorts, event: StoredEvent) -> None:
    task = str(event.payload["task_id"])
    if ports.protected_task_held(task):
        raise ports.unsupported(
            f"legacy release conflicts with protected custody at sequence {event.seq}"
        )
    context.state.claims.pop(task, None)


def _resource(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    payload = event.payload
    offer = ResourceOffer(
        agent=str(payload["agent"]),
        kind=str(payload["kind"]),
        name=str(payload["name"]),
        capacity=int(payload.get("capacity", 1)),
        meta=dict(payload.get("meta", {})),
        offered_at=float(payload["offered_at"]),
    )
    context.state.resources[f"{offer.agent}:{offer.kind}:{offer.name}"] = offer


def _ledger_task(context: ReplayContext, ports: ReplayPorts, event: StoredEvent) -> None:
    task = ports.ledger_task(event.payload)
    context.blackboard.tasks[task.task_id] = task


def _progress(context: ReplayContext, ports: ReplayPorts, event: StoredEvent) -> None:
    context.blackboard.restore_progress(ports.progress(event.payload))


def _chat(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    context.chat_history.append(event.payload)
    context.message_seq = max(context.message_seq, int(event.payload.get("msg_id", 0)))


def _idempotency(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    key = str(event.payload.get("key", ""))
    if key:
        context.idempotency[key] = dict(event.payload.get("response", {}))
        context.idempotency.move_to_end(key)


def _finding(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    provenance = event.payload.get("provenance")
    if isinstance(provenance, dict):
        actor = str(provenance.get("actor") or "").strip()
        if actor:
            counts = context.finding_counts_by_actor
            counts[actor] = counts.get(actor, 0) + 1


def _relay(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    context.relay_approvals.restore_audit(event.payload)


def _corrupt(context: ReplayContext, _ports: ReplayPorts, event: StoredEvent) -> None:
    context.corrupt_rows.append(CorruptEventRow.from_payload(event.seq, event.payload))


def event_appliers(kinds: type[EventKind]) -> dict[str, ReplayApplier]:
    """Bind reconstruction families to the journal's existing event vocabulary.

    Parameters
    ----------
    kinds : type of EventKind
        The owner's canonical tags, including claim snapshot aliases.

    Returns
    -------
    dict of str to callable
        Ordinary supported event appliers. Unknown ordinary kinds are omitted;
        protected kinds use explicitly enrolled restorers before this registry.
    """
    # Checkpoint and handoff persist full post-mutation claim snapshots: the
    # same decoder restores their checkpoint fields and new owner exactly.
    # Recall is telemetry for downstream memory, without coordination authority.
    # Findings restore provenance counts only; their bodies remain memory facts.
    return {
        kinds.CLAIM: _claim,
        kinds.TASK_UPDATE: _claim,
        kinds.CHECKPOINT: _claim,
        kinds.HANDOFF: _claim,
        kinds.RELEASE: _release,
        kinds.RESOURCE: _resource,
        kinds.LEDGER_TASK: _ledger_task,
        kinds.LEDGER_PROGRESS: _progress,
        kinds.CHAT: _chat,
        kinds.IDEMPOTENCY: _idempotency,
        kinds.FINDING: _finding,
        kinds.OPERATOR_RELAY: _relay,
        kinds.CORRUPT: _corrupt,
    }


def apply_event(
    context: ReplayContext,
    ports: ReplayPorts,
    appliers: Mapping[str, ReplayApplier],
    event: StoredEvent,
) -> None:
    """Restore one event, refusing unknown protected custody before ordinary dispatch.

    Parameters
    ----------
    context, ports : ReplayContext, ReplayPorts
        Accumulated state and its existing reconstruction authority.
    appliers : mapping of str to callable
        Ordinary event-family registry.
    event : StoredEvent
        Next retained fact, supplied in journal sequence order.

    Raises
    ------
    UnsupportedProtectedWriteHistoryError
        If protected custody has no enrolled restorer. Unknown ordinary kinds
        remain audit-only and do not acquire coordination authority.
    """
    protected = ports.protected_restorers.get(event.kind)
    if protected is not None:
        protected(event)
        return
    if event.kind == "protected_write" or event.kind.startswith("protected_write_"):
        raise ports.unsupported(
            f"unsupported protected-write history at sequence {event.seq}; "
            "custody reconciliation is required before authority can resume"
        )
    applier = appliers.get(event.kind)
    if applier is not None:
        applier(context, ports, event)
