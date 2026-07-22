# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — map authoritative mutations to/from the durable event log
"""Translate hub state changes to durable events and replay them back.

This module is the bridge between the in-memory coordination state and the
append-only :class:`~synapse_channel.core.persistence.EventStore`. Each authoritative
mutation — a claim, release, task update, resource offer, or chat message — is
recorded as one event; :func:`replay` reads the whole log back and rebuilds the
exact state the hub held, so a restart resumes from durable storage rather than
an empty registry.

Claim and task-update events carry the full claim snapshot, so replay overwrites
the projected claim directly instead of re-deriving it through the claim
algorithm — the persisted epoch and lease are reconstructed faithfully. Lease
durability (``durable=True``) is used for the claim/release/update path; the
high-volume chat and the soft resource offers use ordinary commits.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND, CorruptEventRow
from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    DEFAULT_MAX_PROGRESS_PER_AUTHOR,
    DEFAULT_MAX_PROGRESS_PER_TASK,
    Blackboard,
    LedgerTask,
    ProgressNote,
)
from synapse_channel.core.path_identity import parse_optional_claim_scope_identity
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import (
    MAX_CLAIMS_PER_AGENT,
    MAX_OFFERS_PER_AGENT,
    GitContext,
    ResourceOffer,
    SynapseState,
    TaskClaim,
)


class EventKind:
    """Durable event kind tags written to the log."""

    CLAIM = "claim"
    CLAIM_DENIAL = "claim_denial"
    GUARD_DENIAL = "guard_denial"
    RELEASE = "release"
    TASK_UPDATE = "task_update"
    CHECKPOINT = "checkpoint"
    HANDOFF = "handoff"
    RESOURCE = "resource"
    CHAT = "chat"
    LEDGER_TASK = "ledger_task"
    LEDGER_PROGRESS = "ledger_progress"
    RECALL = "recall"
    FINDING = "finding"
    IDEMPOTENCY = "idempotency"
    SANDBOX_RUN = "sandbox_run"
    OPERATOR_RELAY = "operator_relay"
    DEAD_LETTER_ESCALATION = "dead_letter_escalation"
    DEAD_LETTER_FORWARDING = "dead_letter_forwarding"
    DELIVERY_RECEIPT_REQUESTED = "delivery_receipt_requested"
    DELIVERY_RECEIPT_IMMEDIATE = "delivery_receipt_immediate"
    DELIVERY_RECEIPT_DEFERRED = "delivery_receipt_deferred"
    DELIVERY_RECEIPT_EXPIRED = "delivery_receipt_expired"
    MAILBOX_WATERMARK = "mailbox_watermark"
    IDENTITY_PIN_RECLAIM = "identity_pin_reclaim"
    MULTIHUB_PARTITION = "multihub_partition"
    MULTIHUB_HEAL = "multihub_heal"
    CORRUPT = CORRUPT_EVENT_KIND


_UNVERIFIED_PARTITION_CONTESTER = "<unverified-persisted-contester>"
"""Fail-closed marker for a named partition row with malformed contestants."""


MEMORY_KINDS = frozenset(
    {
        EventKind.RECALL,
        EventKind.FINDING,
        EventKind.CHECKPOINT,
        EventKind.HANDOFF,
    }
)
"""The durable event kinds the persistent-memory read-side ingests.

The query-stream (``recall``), the authored atoms (``finding``), and the
highest-signal episodic state (``checkpoint``/``handoff``) — the subset of the
log a downstream memory adapter reads through the seq-cursored ingest seam. The
pure coordination kinds (``claim``/``release``/``task_update``/``resource``/the
ledger kinds) are excluded; ``chat`` is filtered read-side, not here.
"""


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
    """

    state: SynapseState
    chat_history: list[dict[str, Any]]
    message_seq: int
    blackboard: Blackboard
    idempotency: list[tuple[str, dict[str, Any]]]
    finding_counts_by_actor: dict[str, int]
    corrupt_rows: tuple[CorruptEventRow, ...] = ()


def record_claim(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a claim or renewal."""
    store.append(EventKind.CLAIM, claim.as_persisted_dict(), durable=True)


def record_claim_denial(
    store: EventStore,
    *,
    claimant: str,
    task_id: str,
    reason_code: str,
    worktree: str,
    paths: list[str],
) -> int:
    """Append bounded, content-minimized evidence for a refused claim.

    The audit row deliberately excludes the request note, Git metadata, raw task
    id, and raw paths.  A caller that already knows the attempted identifiers can
    correlate them by digest without turning the evidence log into a second copy
    of repository or prompt content.
    """
    claimant_text = str(claimant)
    scope = json.dumps(
        {"paths": [str(path) for path in paths], "worktree": str(worktree)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {
        "claimant": claimant_text[:256],
        "claimant_sha256": hashlib.sha256(claimant_text.encode("utf-8")).hexdigest(),
        "claimant_truncated": len(claimant_text) > 256,
        "decision": "deny",
        "path_count": len(paths),
        "reason_code": str(reason_code),
        "scope_sha256": hashlib.sha256(scope).hexdigest(),
        "task_id_sha256": hashlib.sha256(str(task_id).encode("utf-8")).hexdigest(),
    }
    return store.append(EventKind.CLAIM_DENIAL, payload, durable=True)


def record_guard_denial(store: EventStore, evidence: Mapping[str, Any]) -> int:
    """Append one validated, digest-only guard refusal as durable evidence."""
    return store.append(EventKind.GUARD_DENIAL, dict(evidence), durable=True)


def record_release(store: EventStore, task_id: str) -> None:
    """Append a durable event marking a task released."""
    store.append(EventKind.RELEASE, {"task_id": task_id}, durable=True)


def record_task_update(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event with the post-update claim snapshot."""
    store.append(EventKind.TASK_UPDATE, claim.as_persisted_dict(), durable=True)


RELAY_DIRECTION_IN = "in"
"""Audit ``direction`` on the hub that *applies* a relayed action (the owning hub)."""

RELAY_DIRECTION_OUT = "out"
"""Audit ``direction`` on the hub that *originates* a relay and forwards it to the owner."""

DEAD_LETTER_DIRECTION_IN = "in"
"""Audit ``direction`` on the owning hub that *receives* a forwarded dead-letter pointer."""

DEAD_LETTER_DIRECTION_OUT = "out"
"""Audit ``direction`` on the origin hub that *forwards* a dead-letter pointer to the owner."""


def record_operator_relay(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a governed cross-hub operator relay.

    This kind is **audit-only**: :func:`replay` skips it, because the state change a
    relay makes (for a release relay, the freed lease) is journalled by the underlying
    coordination event (a ``release``) that reconstructs state on its own. The relay
    event exists so the durable log records the cross-hub *provenance* a coordination
    event never carries — which action was relayed, into which namespace, the verified
    peer it arrived from, and the operator and origin hub it asserts — so a force-release
    performed on one hub's authority by another is attributable after the fact.

    A relay leaves an audit trail on **both** hubs it touches, distinguished by the
    ``direction`` field: :data:`RELAY_DIRECTION_OUT` on the origin hub that forwarded it
    (recording the local requester and the destination owner) and
    :data:`RELAY_DIRECTION_IN` on the owning hub that applied it (recording the verified
    peer and the previous holder), so the two sides of one relay reconcile in the log.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The relay's audit fields (action, namespace, task id, ``direction``, asserted
        operator and origin hub, whether it was applied, and a detail; plus the verified
        peer and previous owner inbound, or the local requester and destination outbound).
    """
    store.append(EventKind.OPERATOR_RELAY, dict(provenance), durable=True)


def record_operator_release(store: EventStore, task_id: str, provenance: Mapping[str, Any]) -> None:
    """Atomically append a force-release projection and its relay provenance.

    Replay needs the ordinary ``release`` row, while auditors need the adjacent
    ``operator_relay`` row. They describe one governed mutation and therefore
    must never commit independently.
    """
    store.append_batch(
        (
            (EventKind.RELEASE, {"task_id": task_id}),
            (EventKind.OPERATOR_RELAY, dict(provenance)),
        ),
        durable=True,
    )


def record_identity_pin_reclaim(store: EventStore, provenance: Mapping[str, Any]) -> int:
    """Append the mandatory durable audit event for an applied pin reclaim.

    This event is audit-only: the identity-pin JSON file is the durable state
    projection, while replay deliberately ignores this kind. The event records
    who removed which exact key, why, whether the live/lease gates were
    overridden, and whether a live socket was evicted, so a break-glass action
    is never confused with passive expiry or a first-use pin.

    Returns
    -------
    int
        Monotonic journal sequence of the audit event.
    """
    return store.append(EventKind.IDENTITY_PIN_RECLAIM, dict(provenance), durable=True)


def record_multihub_ownership_transitions(
    store: EventStore, transitions: list[tuple[str, Mapping[str, Any]]]
) -> tuple[int, ...]:
    """Append one observation round's partition/heal transitions atomically.

    These rows are audit-only: coordination replay skips them, while the standing
    multi-hub watch folds them separately to restore unresolved partition
    suspicion after a restart.  A round is one durable transaction so an
    operator never observes only half of a simultaneous heal/partition change.
    """
    return store.append_batch(transitions, durable=True)


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


def record_dead_letter_escalation(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a dead-letter blackhole crossing its escalation threshold.

    This kind is **audit-only**: :func:`replay` skips it, because it records no coordination state
    change — the dead-letter ledger is rebuilt from live sends, not the log — only the durable fact
    that a target's undelivered count reached an escalation point, so an operator reviewing the log
    can see when a blackhole was flagged and how large it had grown.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The escalation fields: the target, its undelivered count, the most recent sender, and the
        threshold that fired.
    """
    store.append(EventKind.DEAD_LETTER_ESCALATION, dict(provenance), durable=True)


def record_dead_letter_forwarding(store: EventStore, provenance: Mapping[str, Any]) -> None:
    """Append a durable audit event for a dead-letter blackhole forwarded to its owning peer hub.

    Like :func:`record_dead_letter_escalation` this kind is **audit-only**: :func:`replay` skips
    it, because it records no coordination state change — only the durable, attributable fact that
    this hub resolved a blackholed target to a peer's domain and handed the peer a pointer to the
    gap (never a message body). It is written whether or not a live forwarder delivered the signal,
    so a forward that could not reach the peer is still reviewable as "recorded but not delivered".

    A forward leaves an audit trail on **both** hubs it touches, distinguished by the ``direction``
    field: :data:`DEAD_LETTER_DIRECTION_OUT` on the origin hub that resolved the target to a peer
    and forwarded the pointer, and :data:`DEAD_LETTER_DIRECTION_IN` on the owning hub that received
    it (recording the verified sending ``peer``), so the two sides of one forward reconcile in the
    log.

    Parameters
    ----------
    store : EventStore
        The recording hub's durable event store.
    provenance : Mapping[str, Any]
        The forwarding fields: the target, its undelivered count, the origin and owner hub ids, and
        the ``direction``; plus the verified sending ``peer`` on the owning (inbound) side.
    """
    store.append(EventKind.DEAD_LETTER_FORWARDING, dict(provenance), durable=True)


def record_delivery_receipt_requested(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append the durable fact that a sender requested a delivery receipt.

    The payload names the sender, addressed target, per-hub message id, and durable
    chat sequence. It is audit-only — replay skips it — but it gives operators the
    first edge in the receipt lifecycle before the immediate or deferred verdicts.
    """
    store.append(EventKind.DELIVERY_RECEIPT_REQUESTED, dict(receipt), durable=True)


def record_delivery_receipt_immediate(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append the immediate delivery receipt verdict returned to the sender."""
    store.append(EventKind.DELIVERY_RECEIPT_IMMEDIATE, dict(receipt), durable=True)


def record_delivery_receipt_deferred(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append a deferred receipt emitted when a mailbox replay is acked."""
    store.append(EventKind.DELIVERY_RECEIPT_DEFERRED, dict(receipt), durable=True)


def record_delivery_receipt_expired(store: EventStore, receipt: Mapping[str, Any]) -> None:
    """Append a pending receipt expiry when the bounded live window evicts it."""
    store.append(EventKind.DELIVERY_RECEIPT_EXPIRED, dict(receipt), durable=True)


def record_mailbox_watermark(
    store: EventStore,
    *,
    identity: str,
    through_seq: int,
    source: str,
) -> None:
    """Append a receiver-acknowledged mailbox cursor.

    This uses the chat path's normal durability: losing the newest watermark to
    a power failure causes safe replay/recount, never deletion of an unseen body.
    """
    store.append(
        EventKind.MAILBOX_WATERMARK,
        {
            "identity": identity,
            "through_seq": int(through_seq),
            "source": source,
        },
    )


def record_checkpoint(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a resume checkpoint saved on a claim.

    The payload is the full post-checkpoint claim snapshot, so :func:`replay`
    reconstructs the claim — including its durable ``checkpoint`` — exactly as a
    ``claim`` event would. The distinct kind lets the persistent-memory read-side
    pick out resume summaries (the highest-signal episodic memory) without
    re-deriving them from generic claim snapshots; coordination replay treats it
    identically to a claim.
    """
    store.append(EventKind.CHECKPOINT, claim.as_persisted_dict(), durable=True)


def record_handoff(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a task handed to another agent.

    The payload is the full post-handoff claim snapshot — now owned by the
    recipient — so :func:`replay` reconstructs ownership exactly as a ``claim``
    event would. The distinct kind lets the read-side trace ownership transfers
    apart from ordinary claims; coordination replay treats it identically.
    """
    store.append(EventKind.HANDOFF, claim.as_persisted_dict(), durable=True)


def record_resource(store: EventStore, offer: ResourceOffer) -> None:
    """Append a (non-durable) event capturing a resource offer."""
    store.append(
        EventKind.RESOURCE,
        {
            "agent": offer.agent,
            "kind": offer.kind,
            "name": offer.name,
            "capacity": offer.capacity,
            "meta": offer.meta,
            "offered_at": offer.offered_at,
        },
    )


def record_sandbox_run(store: EventStore, receipt: dict[str, Any]) -> None:
    """Append a WASM sandbox run receipt to the durable log as an attestation.

    The receipt is already a bounded, digest-only record — tool id, content
    digest, input/output digests, exit token, fuel used, granted capabilities,
    and any containment reason — so persisting it makes every sandboxed
    execution auditable through the same event query and replay path as a
    claim or a release: an operator can later prove which tool ran under which
    grants and how it exited, without the tool's inputs or outputs ever
    entering the log.
    """
    store.append(EventKind.SANDBOX_RUN, dict(receipt), durable=True)


def record_chat(store: EventStore, message: dict[str, Any]) -> int:
    """Append a (non-durable) event capturing a broadcast chat message.

    Returns the durable ``seq`` the chat was journalled under, so the hub can
    stamp it on the outgoing frame as the resume cursor a reconnecting client
    replays its missed directed backlog from.
    """
    return store.append(EventKind.CHAT, message)


def record_ledger_task(store: EventStore, task: LedgerTask) -> None:
    """Append a durable event with a declared/updated ledger task snapshot."""
    store.append(EventKind.LEDGER_TASK, task.as_dict(), durable=True)


def record_ledger_progress(store: EventStore, note: ProgressNote) -> None:
    """Append a (non-durable) event capturing one progress note."""
    store.append(EventKind.LEDGER_PROGRESS, note.as_dict())


def record_recall(store: EventStore, record: dict[str, Any]) -> None:
    """Append a (non-durable) event capturing one recall query-stream log.

    The record is memory telemetry — the fleet's actual lookups, captured so a
    downstream persistent-memory layer can calibrate recall against the real query
    distribution rather than activity-weighted noise. It is not coordination state,
    so :func:`replay` ignores it; it is committed at ``NORMAL`` durability (a lost
    log on an OS crash is statistically harmless, like a dropped chat line).

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    record : dict[str, Any]
        The recall event body: the query and its outcome, with the producing
        identity and time already hub-attested by the handler.
    """
    store.append(EventKind.RECALL, record)


def record_finding(store: EventStore, record: dict[str, Any]) -> None:
    """Append one finding to the durable memory spine.

    A finding is an authored memory atom — a fact, lesson, decision, dead-end, or
    outcome the producer wants remembered — that has already passed the emit gate
    (:mod:`synapse_channel.core.emit_gate`) and been stamped with its hub-attested
    origin. Unlike recall telemetry, a finding is the durable record a
    persistent-memory adapter ingests, so it is committed at ``FULL`` durability
    (``durable=True``): it must survive an OS crash, like the lease path. It is
    not coordination state, so :func:`replay` skips it when rebuilding the hub's
    working state.

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    record : dict[str, Any]
        The serialised, gate-admitted, hub-attested finding (see
        :meth:`synapse_channel.core.finding.Finding.as_dict`).
    """
    store.append(EventKind.FINDING, record, durable=True)


def record_idempotency(store: EventStore, key: str, response: dict[str, Any]) -> None:
    """Append a durable record of an idempotency key and the response it replays.

    The idempotency cache makes a retried mutation a no-op by replaying the
    original response; held only in memory it is lost on a hub restart, so a
    reconnecting agent's retry would re-apply the mutation. Journalling the
    ``key``/``response`` pair lets :func:`replay` rebuild the cache, so the
    at-most-once guarantee survives a restart. It is committed at ``FULL``
    durability to match the lease mutations it protects: a guard weaker than the
    mutation would let a retry re-apply after an OS crash.

    Parameters
    ----------
    store : EventStore
        The event log to append to.
    key : str
        The client-supplied idempotency key.
    response : dict[str, Any]
        The response message to replay for a future duplicate of ``key``.
    """
    store.append(EventKind.IDEMPOTENCY, {"key": key, "response": response}, durable=True)


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
        checkpoint=str(payload.get("checkpoint", "")),
        git=git,
    )


def replay(
    store: EventStore,
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

    Returns
    -------
    ReplayResult
        The reconstructed state, chat history, highest chat message id, and
        shared blackboard. Unknown event kinds are skipped so the log can evolve
        forwards.
    """
    state = SynapseState(
        default_ttl_seconds=default_ttl_seconds,
        max_claims_per_agent=max_claims_per_agent,
        max_offers_per_agent=max_offers_per_agent,
        max_paths_per_claim=max_paths_per_claim,
    )
    chat_history: list[dict[str, Any]] = []
    blackboard = Blackboard(
        max_progress=max_progress,
        max_progress_per_author=max_progress_per_author,
        max_progress_per_task=max_progress_per_task,
    )
    idempotency: OrderedDict[str, dict[str, Any]] = OrderedDict()
    finding_counts_by_actor: dict[str, int] = {}
    corrupt_rows: list[CorruptEventRow] = []
    message_seq = 0
    epoch_seq = 0

    for event in store.iter_events(through_seq=up_to_seq, kinds=event_kinds):
        payload = event.payload
        if event.kind == EventKind.CORRUPT:
            corrupt_rows.append(CorruptEventRow.from_payload(event.seq, payload))
            continue
        # CHECKPOINT and HANDOFF carry the full claim snapshot, distinct only so
        # the memory read-side can pick them out — coordination replay reconstructs
        # the claim from them exactly as it does a CLAIM/TASK_UPDATE (and a legacy
        # log that journalled them as CLAIM still replays through this same branch).
        if event.kind in (
            EventKind.CLAIM,
            EventKind.TASK_UPDATE,
            EventKind.CHECKPOINT,
            EventKind.HANDOFF,
        ):
            claim = _claim_from_payload(payload)
            state.claims[claim.task_id] = claim
            state.last_seen[claim.owner] = claim.claimed_at
            epoch_seq = max(epoch_seq, claim.epoch)
        elif event.kind == EventKind.LEDGER_TASK:
            task = _ledger_task_from_payload(payload)
            blackboard.tasks[task.task_id] = task
        elif event.kind == EventKind.LEDGER_PROGRESS:
            blackboard.restore_progress(_progress_from_payload(payload))
        elif event.kind == EventKind.RELEASE:
            state.claims.pop(str(payload["task_id"]), None)
        elif event.kind == EventKind.RESOURCE:
            offer = ResourceOffer(
                agent=str(payload["agent"]),
                kind=str(payload["kind"]),
                name=str(payload["name"]),
                capacity=int(payload.get("capacity", 1)),
                meta=dict(payload.get("meta", {})),
                offered_at=float(payload["offered_at"]),
            )
            state.resources[f"{offer.agent}:{offer.kind}:{offer.name}"] = offer
        elif event.kind == EventKind.CHAT:
            chat_history.append(payload)
            message_seq = max(message_seq, int(payload.get("msg_id", 0)))
        elif event.kind == EventKind.IDEMPOTENCY:
            key = str(payload.get("key", ""))
            if key:
                idempotency[key] = dict(payload.get("response", {}))
                idempotency.move_to_end(key)  # latest response, most-recently-used
        elif event.kind == EventKind.FINDING:
            provenance = payload.get("provenance")
            if isinstance(provenance, dict):
                actor = str(provenance.get("actor") or "").strip()
                if actor:
                    finding_counts_by_actor[actor] = finding_counts_by_actor.get(actor, 0) + 1
        # RECALL is telemetry and FINDING is the durable memory spine. Neither is
        # coordination state, so replay never inserts them into the registry; findings
        # only seed live quota counters for the same actor after a restart.

    state._epoch_seq = epoch_seq
    # Replay assigns claims straight into the registry, so build the lease-expiry
    # heap from the reconstructed claims before any expiry pass runs against it.
    state.reindex_leases()
    ts = time.time() if now is None else float(now)
    state._expire_claims(ts)
    state._expire_resources(ts)
    return ReplayResult(
        state=state,
        chat_history=chat_history,
        message_seq=message_seq,
        blackboard=blackboard,
        idempotency=list(idempotency.items()),
        finding_counts_by_actor=dict(finding_counts_by_actor),
        corrupt_rows=tuple(corrupt_rows),
    )
