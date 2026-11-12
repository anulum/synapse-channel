# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded current-owned work pages
"""Read bounded current-owned work without materialising global snapshots."""

from __future__ import annotations

import hashlib
import json

from synapse_channel.core.ledger import Blackboard, LedgerTask
from synapse_channel.core.owned_index import OwnedEntry
from synapse_channel.core.owned_work import OwnedQuery
from synapse_channel.core.state import SynapseState, TaskClaim


def _record(
    board: Blackboard, state: SynapseState, entry: OwnedEntry, now: float
) -> LedgerTask | TaskClaim | None:
    """Check current ownership, scope and lease before serialising a public record."""
    if entry.kind == "tasks":
        task = board.tasks.get(entry.task_id)
        if (
            task is not None
            and task.suggested_owner == entry.owner
            and task.status not in ("done", "cancelled")
            and (
                not entry.project
                or (task.project == entry.project and task.task_id.startswith(entry.project + "/"))
            )
        ):
            return task
    else:
        claim = state.claims.get(entry.task_id)
        if (
            claim is not None
            and claim.owner == entry.owner
            and claim.status not in ("done", "failed")
            and claim.lease_expires_at > now
        ):
            return claim
    return None


def _list_work(
    board: Blackboard,
    state: SynapseState,
    query: OwnedQuery,
    binding: dict[str, object],
    now: float,
) -> dict[str, object]:
    """Seek bounded metadata in just this owner's selected project buckets."""
    indexes = (board.owned_tasks, state.owned_claims)
    revision = hashlib.sha256(
        "".join(index.revision(query.identity, query.project) for index in indexes).encode()
    ).hexdigest()
    if query.revision and query.revision != revision:
        raise ValueError("owned work changed; restart listing")
    entries = sorted(
        (
            entry
            for index in indexes
            for entry in index.page(
                query.identity, query.project, after_key=query.after_key, limit=query.limit + 1
            )
        ),
        key=lambda entry: entry.key,
    )
    scanned = entries[: query.limit]
    return {
        **binding,
        "revision": revision,
        "cursor": scanned[-1].key if scanned else query.after_key,
        "has_more": len(entries) > query.limit,
        "items": [
            entry.metadata() for entry in scanned if _record(board, state, entry, now) is not None
        ],
    }


def _read_work(
    board: Blackboard,
    state: SynapseState,
    query: OwnedQuery,
    binding: dict[str, object],
    now: float,
) -> dict[str, object]:
    """Bind every bounded fragment to a current generation and complete body digest."""
    indexes = (board.owned_tasks, state.owned_claims)
    entry = next(
        (
            entry
            for index in indexes
            if (entry := index.get(query.identity, query.project, query.key)) is not None
        ),
        None,
    )
    if entry is None or entry.version != query.item_version:
        raise ValueError("owned work no longer available")
    record = _record(board, state, entry, now)
    if record is None:
        raise ValueError("owned work no longer available")
    content = json.dumps(
        record.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    digest = hashlib.sha256(content.encode("ascii")).hexdigest()
    if (query.digest and query.digest != digest) or query.offset >= len(content):
        raise ValueError("owned record changed or offset invalid")
    end = min(len(content), query.offset + query.limit_chars)
    return {
        **binding,
        **entry.metadata(),
        "offset": query.offset,
        "next_offset": end,
        "total_chars": len(content),
        "has_more": end < len(content),
        "content_sha256": digest,
        "content": content[query.offset : end],
    }


def read_owned_work(
    board: Blackboard, state: SynapseState, query: OwnedQuery, *, hub_id: str, now: float
) -> dict[str, object]:
    """Return a bounded owner-scoped list or a lossless canonical-JSON fragment.

    List revisions fence owner/project membership and process restarts. A record
    generation and content SHA-256 fence every read. Join ASCII fragments before
    parsing JSON; this read never acknowledges processing or materialises global
    board/state snapshots.
    """
    if query.hub_id and query.hub_id != hub_id:
        raise ValueError("owned work hub changed")
    binding: dict[str, object] = {
        "version": 1,
        "available": True,
        "identity": query.identity,
        "hub_id": hub_id,
        "project": query.project,
        "mode": query.mode,
    }
    if query.mode == "list":
        return _list_work(board, state, query, binding, now)
    return _read_work(board, state, query, binding, now)
