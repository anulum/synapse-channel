# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — map authoritative mutations to/from the durable event log
"""Translate hub state changes to durable events and replay them back.

This module is the bridge between the in-memory coordination state and the
append-only :class:`~synapse_channel.persistence.EventStore`. Each authoritative
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
from dataclasses import dataclass
from typing import Any

from synapse_channel.persistence import EventStore
from synapse_channel.state import ResourceOffer, SynapseState, TaskClaim


class EventKind:
    """Durable event kind tags written to the log."""

    CLAIM = "claim"
    RELEASE = "release"
    TASK_UPDATE = "task_update"
    RESOURCE = "resource"
    CHAT = "chat"


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
    """

    state: SynapseState
    chat_history: list[dict[str, Any]]
    message_seq: int


def record_claim(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event capturing a claim or renewal."""
    store.append(EventKind.CLAIM, claim.as_dict(), durable=True)


def record_release(store: EventStore, task_id: str) -> None:
    """Append a durable event marking a task released."""
    store.append(EventKind.RELEASE, {"task_id": task_id}, durable=True)


def record_task_update(store: EventStore, claim: TaskClaim) -> None:
    """Append a durable event with the post-update claim snapshot."""
    store.append(EventKind.TASK_UPDATE, claim.as_dict(), durable=True)


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


def _claim_from_payload(payload: dict[str, Any]) -> TaskClaim:
    """Rebuild a :class:`TaskClaim` from a persisted claim snapshot."""
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
    )


def replay(
    store: EventStore,
    *,
    default_ttl_seconds: float = 3600.0,
    now: float | None = None,
) -> ReplayResult:
    """Rebuild coordination state by replaying the whole event log.

    Parameters
    ----------
    store : EventStore
        The event log to read.
    default_ttl_seconds : float, optional
        TTL seeded into the reconstructed :class:`SynapseState`.
    now : float or None, optional
        Wall-clock time used to expire stale leases/offers after replay; the
        system clock is used when ``None``.

    Returns
    -------
    ReplayResult
        The reconstructed state, chat history, and highest chat message id.
        Unknown event kinds are skipped so the log can evolve forwards.
    """
    state = SynapseState(default_ttl_seconds=default_ttl_seconds)
    chat_history: list[dict[str, Any]] = []
    message_seq = 0
    epoch_seq = 0

    for event in store.read_all():
        payload = event.payload
        if event.kind in (EventKind.CLAIM, EventKind.TASK_UPDATE):
            claim = _claim_from_payload(payload)
            state.claims[claim.task_id] = claim
            state.last_seen[claim.owner] = claim.claimed_at
            epoch_seq = max(epoch_seq, claim.epoch)
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

    state._epoch_seq = epoch_seq
    ts = time.time() if now is None else float(now)
    state._expire_claims(ts)
    state._expire_resources(ts)
    return ReplayResult(state=state, chat_history=chat_history, message_seq=message_seq)
