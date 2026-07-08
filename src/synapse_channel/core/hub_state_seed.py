# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — resume the hub's durable state from the log, or start fresh
"""Durable-state seeding for the routing hub.

:func:`seed_hub_state` decides how a hub begins: replaying its durable log to
resume live leases, chat history, the shared blackboard, and the ledger-guard seed
(the message-id high-water mark, per-actor finding counts, and the idempotency
cache) so a restart continues where it left off, or building an empty registry when
no journal is attached. It also emits the one-off startup hint when a hub is opened
on a log larger than the compaction threshold, since the log grows append-only and
is never auto-compacted.

The seeding is a pure function of the journal and the retention bounds — it holds no
hub reference and returns a :class:`SeededHubState` the hub binds to itself — so the
resume-versus-fresh decision is testable without constructing a live hub. The hint
is logged through a logger named ``synapse.hub`` so its record stays under the hub's
log namespace.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.delivery_receipts import (
    DELIVERY_RECEIPT_EVENT_KINDS,
    restore_pending_receipts,
)
from synapse_channel.core.journal import replay
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState

logger = logging.getLogger("synapse.hub")


@dataclass(frozen=True)
class SeededHubState:
    """The durable state a hub begins with, resumed from a log or built empty.

    Attributes
    ----------
    state : SynapseState
        The lease/claim/offer registry, replayed or fresh.
    chat_history : list[dict]
        Retained chat messages, trimmed to the history bound (empty when fresh).
    blackboard : Blackboard
        The shared progress board, replayed or fresh.
    message_seq : int
        The per-hub message-id high-water mark to resume from (``0`` when fresh).
    finding_counts : Mapping[str, int]
        Per-actor durable-finding counts to resume the quota from (empty when fresh).
    idempotency_seed : tuple[tuple[str, dict], ...]
        Applied-mutation responses (oldest first) to reseed the at-most-once cache
        (empty when fresh).
    pending_receipts : tuple[tuple[int, ReceiptEntry], ...]
        Unsettled deferred-delivery receipts reconstructed from the receipt
        ledger, oldest first, ready to seed the live bounded pending store.
    """

    state: SynapseState
    chat_history: list[dict[str, Any]]
    blackboard: Blackboard
    message_seq: int
    finding_counts: Mapping[str, int]
    idempotency_seed: tuple[tuple[str, dict[str, Any]], ...]
    pending_receipts: tuple[tuple[int, ReceiptEntry], ...]


def seed_hub_state(
    journal: EventStore | None,
    *,
    default_ttl_seconds: float,
    max_history: int,
    max_progress: int,
    max_progress_per_author: int,
    max_progress_per_task: int,
    max_claims_per_agent: int,
    max_offers_per_agent: int,
    max_paths_per_claim: int,
    compact_hint_threshold: int,
) -> SeededHubState:
    """Resume the hub's durable state from a journal replay, or start fresh.

    Parameters
    ----------
    journal : EventStore or None
        When given, the durable log is replayed and the hub resumes from it; when
        ``None`` an empty registry, history, and blackboard are built.
    default_ttl_seconds : float
        Lease TTL passed to the replay or the fresh :class:`SynapseState`.
    max_history : int
        Chat-history bound; a replayed history is trimmed to its last this-many.
    max_progress, max_progress_per_author, max_progress_per_task : int
        Blackboard retention bounds, applied on replay or when building fresh.
    max_claims_per_agent, max_offers_per_agent, max_paths_per_claim : int
        Per-agent claim/offer/path bounds passed to the replay or fresh state.
    compact_hint_threshold : int
        Record count past which a resumed hub logs the one-off ``synapse compact``
        hint (already clamped by the caller).

    Returns
    -------
    SeededHubState
        The state, history, blackboard, and ledger-guard seed the hub begins with.
    """
    if journal is not None:
        replayed = replay(
            journal,
            default_ttl_seconds=default_ttl_seconds,
            max_progress=max_progress,
            max_progress_per_author=max_progress_per_author,
            max_progress_per_task=max_progress_per_task,
            max_claims_per_agent=max_claims_per_agent,
            max_offers_per_agent=max_offers_per_agent,
            max_paths_per_claim=max_paths_per_claim,
        )
        # The durable log is append-only and never auto-compacted (pruning is safe
        # only below a sequence the read-side has consumed, which the hub cannot
        # know); a hub started on an oversized log emits one hint to compact manually.
        record_count = journal.count()
        if record_count > compact_hint_threshold:
            logger.warning(
                "Event log holds %d records (over the %d hint threshold); it grows "
                "append-only and is never auto-compacted. Run `synapse compact <db>` "
                "to bound it — safe only below a sequence the read-side has consumed.",
                record_count,
                compact_hint_threshold,
            )
        # Seeded oldest first, the bounded cache keeps the most-recent keys, so a
        # retry after a restart replays the original response instead of re-applying.
        return SeededHubState(
            state=replayed.state,
            chat_history=replayed.chat_history[-max_history:],
            blackboard=replayed.blackboard,
            message_seq=replayed.message_seq,
            finding_counts=replayed.finding_counts_by_actor,
            idempotency_seed=tuple(replayed.idempotency),
            pending_receipts=restore_pending_receipts(
                journal.read_window(kinds=DELIVERY_RECEIPT_EVENT_KINDS)
            ),
        )
    return SeededHubState(
        state=SynapseState(
            default_ttl_seconds=default_ttl_seconds,
            max_claims_per_agent=max_claims_per_agent,
            max_offers_per_agent=max_offers_per_agent,
            max_paths_per_claim=max_paths_per_claim,
        ),
        chat_history=[],
        blackboard=Blackboard(
            max_progress=max_progress,
            max_progress_per_author=max_progress_per_author,
            max_progress_per_task=max_progress_per_task,
        ),
        message_seq=0,
        finding_counts={},
        idempotency_seed=(),
        pending_receipts=(),
    )
