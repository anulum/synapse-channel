# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub restart replay and multi-hub durable state restores
"""Rebuild live hub authority state and multi-hub watches from the durable event log.

This module owns the read side of :mod:`synapse_channel.core.journal`: full restart
replay (:func:`replay` and :class:`ReplayResult`) and the two multi-hub watch/follower
restore folds (:func:`restore_active_multihub_partitions` and
:func:`restore_active_multihub_quarantines`). It is deliberately separate from
:mod:`synapse_channel.core.replay`, which reconstructs one task's history for forks.

Dependency direction: ``journal_replay`` imports vocabulary and
:class:`~synapse_channel.core.journal.UnsupportedProtectedWriteHistoryError` from
:mod:`synapse_channel.core.journal`; :mod:`synapse_channel.core.journal` never imports
this module.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.assignment_origin import ASSIGNMENT_ORIGIN_FIELD, AssignmentOrigin
from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.journal import EventKind, UnsupportedProtectedWriteHistoryError
from synapse_channel.core.journal_appliers import (
    ReplayContext,
    ReplayPorts,
    apply_event,
    event_appliers,
)
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
    Blackboard,
    LedgerTask,
    ProgressNote,
)
from synapse_channel.core.multihub_equivocation import FederationQuarantine, valid_fingerprint
from synapse_channel.core.operator_relay_approval import RelayApprovalLedger
from synapse_channel.core.path_identity import parse_optional_claim_scope_identity
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.persistence_reader import ReadableEventLog
from synapse_channel.core.protected_write_admission_journal import (
    ADMISSION_EVENT_KIND,
    ProtectedAdmissionReplayPolicy,
    restore_protected_admission,
)
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    REVOKE_EVENT_KIND,
    apply_enrollment_event,
)
from synapse_channel.core.protected_write_recovery_journal import (
    RECOVERY_EVENT_KIND,
    restore_protected_recovery,
)
from synapse_channel.core.protected_write_transition_journal import (
    TRANSITION_EVENT_KIND,
    restore_protected_transition,
)
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAX_OFFERS_PER_AGENT,
    GitContext,
    SynapseState,
    TaskClaim,
)

_UNVERIFIED_PARTITION_CONTESTER = "<unverified-persisted-contester>"
"""Fail-closed marker for a named partition row with malformed contestants."""


@dataclass
class ReplayResult:
    """The state reconstructed from a full replay of the event log.

    Attributes
    ----------
    state : SynapseState
        Registry rebuilt from the log, with stale leases and offers expired.
    chat_history : list[dict[str, Any]]
        Replayed chat messages in order.
    message_seq : int
        Highest chat ``msg_id`` seen, so the hub continues numbering without
        collision.
    blackboard : Blackboard
        Shared plan and progress stream rebuilt from the log.
    idempotency : list[tuple[str, dict[str, Any]]]
        Reconstructed idempotency entries, oldest first and deduplicated to the
        latest response per key, ready to seed the hub's bounded cache so the
        at-most-once guarantee survives a restart.
    finding_counts_by_actor : dict[str, int]
        Count of replayed durable findings per hub-attested actor, used to seed
        live per-agent finding quotas after a restart.
    corrupt_rows : tuple[CorruptEventRow, ...]
        Quarantined rows skipped during reconstruction. Any member means the
        projected state is incomplete and must not admit further mutations.
    relay_approvals : RelayApprovalLedger
        Validated pending two-person relay approvals reconstructed from inbound
        audit transitions. Terminal audits remove their matching pending key.
    """

    state: SynapseState
    chat_history: list[dict[str, Any]]
    message_seq: int
    blackboard: Blackboard
    idempotency: list[tuple[str, dict[str, Any]]]
    finding_counts_by_actor: dict[str, int]
    corrupt_rows: tuple[CorruptEventRow, ...] = ()
    relay_approvals: RelayApprovalLedger = field(default_factory=RelayApprovalLedger)


def restore_active_multihub_partitions(store: EventStore) -> dict[str, tuple[str, ...]]:
    """Fold durable partition/heal events into the unresolved partition set.

    A named partition row with malformed contestants restores a synthetic
    contestant, so malformed evidence cannot silently reopen grants. A heal is
    applied only when it carries the successful-observation marker written by
    :class:`~synapse_channel.core.multihub_watch.MultiHubWatch`.
    """
    active: dict[str, tuple[str, ...]] = {}
    for event in store.iter_events(kinds=(EventKind.MULTIHUB_PARTITION, EventKind.MULTIHUB_HEAL)):
        namespace = str(event.payload.get("namespace") or "").strip()
        if not namespace:
            continue
        if event.kind == EventKind.MULTIHUB_HEAL:
            if event.payload.get("observation_refreshed") is True:
                active.pop(namespace, None)
            continue
        raw_contesting = event.payload.get("contesting_hubs")
        if not isinstance(raw_contesting, list):
            active[namespace] = (_UNVERIFIED_PARTITION_CONTESTER,)
            continue
        contesting = tuple(sorted({str(hub).strip() for hub in raw_contesting if str(hub).strip()}))
        active[namespace] = contesting or (_UNVERIFIED_PARTITION_CONTESTER,)
    return active


def restore_active_multihub_quarantines(
    store: EventStore,
) -> dict[str, FederationQuarantine]:
    """Fold durable equivocation/recovery rows into active peer quarantines."""
    active: dict[str, FederationQuarantine] = {}
    kinds = (EventKind.MULTIHUB_EQUIVOCATION, EventKind.MULTIHUB_EQUIVOCATION_RECOVERY)
    for event in store.iter_events(kinds=kinds):
        peer_id = str(event.payload.get("peer_id") or "").strip()
        if not peer_id:
            continue
        if event.kind == EventKind.MULTIHUB_EQUIVOCATION_RECOVERY:
            if (
                event.payload.get("explicit_recovery") is True
                and str(event.payload.get("new_log_generation") or "").strip()
            ):
                active.pop(peer_id, None)
            continue
        seq_raw = event.payload.get("seq")
        seq = seq_raw if isinstance(seq_raw, int) and not isinstance(seq_raw, bool) else 0
        accepted = event.payload.get("accepted_fingerprint")
        conflicting = event.payload.get("conflicting_fingerprint")
        active[peer_id] = FederationQuarantine(
            peer_id=peer_id,
            seq=seq,
            accepted_fingerprint=(accepted if valid_fingerprint(accepted) else "0" * 64),
            conflicting_fingerprint=(conflicting if valid_fingerprint(conflicting) else "0" * 64),
            detected_at=float(event.ts),
            observer_id=str(event.payload.get("observer_id") or ""),
        )
    return active


def _ledger_task_from_payload(payload: dict[str, Any]) -> LedgerTask:
    """Rebuild a :class:`LedgerTask` from a persisted snapshot."""
    return LedgerTask(
        task_id=str(payload["task_id"]),
        title=str(payload["title"]),
        created_at=float(payload["created_at"]),
        updated_at=float(payload["updated_at"]),
        description=str(payload.get("description", "")),
        depends_on=tuple(str(d) for d in payload.get("depends_on", ())),
        status=str(payload.get("status", "open")),
        suggested_owner=str(payload.get("suggested_owner", "")),
        project=str(payload.get("project", "")),
        version=int(payload.get("version", 1)),
        created_by=str(payload.get("created_by", "")),
        assignment_origin=AssignmentOrigin.from_value(payload.get(ASSIGNMENT_ORIGIN_FIELD)),
    )


def _progress_from_payload(payload: dict[str, Any]) -> ProgressNote:
    """Rebuild a :class:`ProgressNote` from a persisted snapshot."""
    return ProgressNote(
        task_id=str(payload.get("task_id", "")),
        author=str(payload.get("author", "")),
        kind=str(payload.get("kind", "note")),
        text=str(payload.get("text", "")),
        posted_at=float(payload["posted_at"]),
    )


def _claim_from_payload(payload: dict[str, Any]) -> TaskClaim:
    """Rebuild a :class:`TaskClaim` from a persisted claim snapshot."""
    raw_git = payload.get("git")
    git = GitContext.from_dict(raw_git) if isinstance(raw_git, dict) else None
    path_identity = parse_optional_claim_scope_identity(payload)
    paths = tuple(str(p) for p in payload.get("paths", ()))
    worktree = str(payload.get("worktree", ""))
    if path_identity is not None and not path_identity.validates_display_scope(worktree, paths):
        raise ValueError("persisted claim path identity does not match its display scope")
    ownership_epoch = payload.get("ownership_epoch", payload.get("epoch", 0))
    if type(ownership_epoch) is not int or not 0 <= ownership_epoch <= int(payload.get("epoch", 0)):
        raise ValueError("invalid persisted ownership epoch")
    return TaskClaim(
        task_id=str(payload["task_id"]),
        owner=str(payload["owner"]),
        note=str(payload.get("note", "")),
        claimed_at=float(payload["claimed_at"]),
        lease_expires_at=float(payload["lease_expires_at"]),
        # Old logs predate principal-bound quotas. Charging those claims to their
        # recorded owner preserves the historical per-agent behaviour without
        # granting a restart-time free budget.
        quota_principal=str(payload.get("quota_principal") or payload["owner"]),
        status=str(payload.get("status", "claimed")),
        data_ref=str(payload.get("data_ref", "")),
        worktree=worktree,
        paths=paths,
        path_identity=path_identity,
        epoch=int(payload.get("epoch", 0)),
        ownership_epoch=ownership_epoch,
        version=int(payload.get("version", 0)),
        checkpoint=str(payload.get("checkpoint", "")),
        git=git,
    )


def _protected_replay_callbacks(
    store: ReadableEventLog,
    state: SynapseState,
    policies: Mapping[str, ProtectedAdmissionReplayPolicy] | None,
    through_seq: int | None,
) -> dict[str, Callable[[StoredEvent], None]]:
    """Retain only explicitly supported restorers and defer their existing hooks."""
    if policies is None:
        return {}
    enrolled = policies
    return {
        ISSUE_EVENT_KIND: lambda event: apply_enrollment_event(event, state),
        REVOKE_EVENT_KIND: lambda event: apply_enrollment_event(event, state),
        RECOVERY_EVENT_KIND: lambda event: restore_protected_recovery(
            event, store=store, state=state, policies=enrolled, through_seq=through_seq
        ),
        TRANSITION_EVENT_KIND: lambda event: restore_protected_transition(
            event, store=store, state=state, policies=enrolled, through_seq=through_seq
        ),
        ADMISSION_EVENT_KIND: lambda event: restore_protected_admission(
            event, store=store, state=state, policies=enrolled, through_seq=through_seq
        ),
    }


def replay(
    store: ReadableEventLog,
    *,
    default_ttl_seconds: float = 3600.0,
    max_progress: int = DEFAULT_MAX_PROGRESS,
    max_progress_per_author: int = DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    max_progress_per_task: int = DEFAULT_MAX_PROGRESS_PER_TASK,
    max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
    max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
    max_paths_per_claim: int = MAX_DECLARED_PATHS,
    now: float | None = None,
    up_to_seq: int | None = None,
    event_kinds: Iterable[str] | None = None,
    protected_write_policies: Mapping[str, ProtectedAdmissionReplayPolicy] | None = None,
) -> ReplayResult:
    """Rebuild coordination state by replaying the whole event log.

    Parameters
    ----------
    store : EventStore
        The event log to read.
    default_ttl_seconds : float, optional
        TTL seeded into the reconstructed :class:`SynapseState`.
    max_progress : int, optional
        Progress-note bound seeded into the reconstructed :class:`Blackboard`.
    max_progress_per_author : int, optional
        Per-author progress-note bound seeded into the reconstructed blackboard.
    max_progress_per_task : int, optional
        Per-task progress-note bound seeded into the reconstructed blackboard.
    max_claims_per_agent : int, optional
        Per-agent claim quota seeded into the reconstructed :class:`SynapseState`.
    max_offers_per_agent : int, optional
        Per-agent offer quota seeded into the reconstructed :class:`SynapseState`.
    max_paths_per_claim : int, optional
        Per-claim declared-path cap seeded into the reconstructed
        :class:`SynapseState`.
    now : float or None, optional
        Wall-clock time used to expire stale leases/offers after replay; the
        system clock is used when ``None``.

    up_to_seq : int or None, optional
        Replay only events with ``seq <= up_to_seq``; ``None`` replays the whole
        log. Bounds the reconstruction to a point in time for a state-at-seq view.
    event_kinds : iterable of str or None, optional
        Ask the event store to decode only these kinds. ``None`` preserves full
        restart replay. Read-side projections may use a proven subset when they
        do not expose chat, idempotency, or memory counters.
    protected_write_policies:
        Explicit retained enrollment policies. Omitted means protected history
        is unsupported. Authority reconstruction requires unfiltered event kinds.

    Returns
    -------
    ReplayResult
        The reconstructed state, chat history, highest chat message id, and
        shared blackboard. Unknown ordinary event kinds are skipped so the log
        can evolve forwards; unsupported protected-write custody is never skipped.

    Raises
    ------
    UnsupportedProtectedWriteHistoryError
        If the selected prefix contains protected-write history this version
        cannot restore. No partially reconstructed authority state is returned.
    """
    if protected_write_policies is not None and event_kinds is not None:
        raise ValueError("protected authority replay requires unfiltered history")
    state = SynapseState(
        default_ttl_seconds=default_ttl_seconds,
        max_claims_per_agent=max_claims_per_agent,
        max_offers_per_agent=max_offers_per_agent,
        max_paths_per_claim=max_paths_per_claim,
    )
    blackboard = Blackboard(
        max_progress=max_progress,
        max_progress_per_author=max_progress_per_author,
        max_progress_per_task=max_progress_per_task,
    )
    context = ReplayContext(
        state=state,
        blackboard=blackboard,
        chat_history=[],
        idempotency=OrderedDict(),
        finding_counts_by_actor={},
        relay_approvals=RelayApprovalLedger(),
        corrupt_rows=[],
    )
    ports = ReplayPorts(
        claim=lambda payload: _claim_from_payload(payload),
        ledger_task=lambda payload: _ledger_task_from_payload(payload),
        progress=lambda payload: _progress_from_payload(payload),
        protected_task_held=lambda task: state.protected_task_held(task),
        protected_scope_held=lambda claim: state.protected_scope_held(
            claim.task_id, claim.worktree, claim.paths, claim.path_identity
        ),
        scope_checked_kinds=frozenset({EventKind.CLAIM, EventKind.HANDOFF}),
        protected_restorers=_protected_replay_callbacks(
            store, state, protected_write_policies, up_to_seq
        ),
        unsupported=lambda detail: UnsupportedProtectedWriteHistoryError(detail),
    )
    appliers = event_appliers(EventKind)
    for event in store.iter_events(through_seq=up_to_seq, kinds=event_kinds):
        apply_event(context, ports, appliers, event)

    state.restore_epoch_seq(context.epoch_seq)
    # Replay assigns claims straight into the registry, so build the lease-expiry
    # heap from the reconstructed claims before any expiry pass runs against it.
    state.reindex_leases()
    ts = time.time() if now is None else float(now)
    state.expire_stale(ts)
    return ReplayResult(
        state=state,
        chat_history=context.chat_history,
        message_seq=context.message_seq,
        blackboard=blackboard,
        idempotency=list(context.idempotency.items()),
        finding_counts_by_actor=dict(context.finding_counts_by_actor),
        relay_approvals=context.relay_approvals,
        corrupt_rows=tuple(context.corrupt_rows),
    )
