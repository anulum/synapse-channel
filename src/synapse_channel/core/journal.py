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

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.ledger import (
    DEFAULT_MAX_PROGRESS,
    Blackboard,
    LedgerTask,
    ProgressNote,
)
from synapse_channel.core.persistence import EventStore
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
    """

    state: SynapseState
    chat_history: list[dict[str, Any]]
    message_seq: int
    blackboard: Blackboard
    idempotency: list[tuple[str, dict[str, Any]]]


def record_claim(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a claim or renewal."""
    store.append(EventKind.CLAIM, claim.as_dict(), durable=True)


def record_release(store: EventStore, task_id: str) -> None:
    """Append a durable event marking a task released."""
    store.append(EventKind.RELEASE, {"task_id": task_id}, durable=True)


def record_task_update(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event with the post-update claim snapshot."""
    store.append(EventKind.TASK_UPDATE, claim.as_dict(), durable=True)


def record_checkpoint(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a resume checkpoint saved on a claim.

    The payload is the full post-checkpoint claim snapshot, so :func:`replay`
    reconstructs the claim — including its durable ``checkpoint`` — exactly as a
    ``claim`` event would. The distinct kind lets the persistent-memory read-side
    pick out resume summaries (the highest-signal episodic memory) without
    re-deriving them from generic claim snapshots; coordination replay treats it
    identically to a claim.
    """
    store.append(EventKind.CHECKPOINT, claim.as_dict(), durable=True)


def record_handoff(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a task handed to another agent.

    The payload is the full post-handoff claim snapshot — now owned by the
    recipient — so :func:`replay` reconstructs ownership exactly as a ``claim``
    event would. The distinct kind lets the read-side trace ownership transfers
    apart from ordinary claims; coordination replay treats it identically.
    """
    store.append(EventKind.HANDOFF, claim.as_dict(), durable=True)


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


def record_chat(store: EventStore, message: dict[str, Any]) -> None:
    """Append a (non-durable) event capturing a broadcast chat message."""
    store.append(EventKind.CHAT, message)


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
    return TaskClaim(
        task_id=str(payload["task_id"]),
        owner=str(payload["owner"]),
        note=str(payload.get("note", "")),
        claimed_at=float(payload["claimed_at"]),
        lease_expires_at=float(payload["lease_expires_at"]),
        status=str(payload.get("status", "claimed")),
        data_ref=str(payload.get("data_ref", "")),
        worktree=str(payload.get("worktree", "")),
        paths=tuple(str(p) for p in payload.get("paths", ())),
        epoch=int(payload.get("epoch", 0)),
        checkpoint=str(payload.get("checkpoint", "")),
        git=git,
    )


def replay(
    store: EventStore,
    *,
    default_ttl_seconds: float = 3600.0,
    max_progress: int = DEFAULT_MAX_PROGRESS,
    max_claims_per_agent: int = MAX_CLAIMS_PER_AGENT,
    max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
    now: float | None = None,
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
    max_claims_per_agent : int, optional
        Per-agent claim quota seeded into the reconstructed :class:`SynapseState`.
    max_offers_per_agent : int, optional
        Per-agent offer quota seeded into the reconstructed :class:`SynapseState`.
    now : float or None, optional
        Wall-clock time used to expire stale leases/offers after replay; the
        system clock is used when ``None``.

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
    )
    chat_history: list[dict[str, Any]] = []
    blackboard = Blackboard(max_progress=max_progress)
    idempotency: OrderedDict[str, dict[str, Any]] = OrderedDict()
    message_seq = 0
    epoch_seq = 0

    for event in store.read_all():
        payload = event.payload
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
            blackboard.progress.append(_progress_from_payload(payload))
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
        # EventKind.RECALL and EventKind.FINDING are the memory layer's telemetry
        # and durable spine, not coordination state — they are journalled for the
        # read-side ingest seam and deliberately skipped here, so a restart never
        # replays them into the registry.

    state._epoch_seq = epoch_seq
    # Replay assigns claims straight into the registry, so build the lease-expiry
    # heap from the reconstructed claims before any expiry pass runs against it.
    state.reindex_leases()
    ts = time.time() if now is None else float(now)
    state._expire_claims(ts)
    state._expire_resources(ts)
    # Trim replayed progress to the bound (the durable log keeps every note).
    if len(blackboard.progress) > blackboard.max_progress:
        del blackboard.progress[: len(blackboard.progress) - blackboard.max_progress]
    return ReplayResult(
        state=state,
        chat_history=chat_history,
        message_seq=message_seq,
        blackboard=blackboard,
        idempotency=list(idempotency.items()),
    )
