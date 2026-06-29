# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fold a merged multi-hub event log into an observed mergeable view
"""Fold a merged multi-hub event log into an *observed* mergeable view.

:mod:`synapse_channel.core.multihub_merge` produces the deterministic union of several
hubs' event logs. This module folds that ordered stream into the coordination state
that is safe to merge (`docs/multi-hub-sync.md`): the shared **board** (last-writer-wins
per task, so the latest declaration of a task wins), the **progress** ledger (grow-only,
every note kept in order), and the **observed claim** view.

The claim view is the safety-critical part. Claims are mutual exclusion, not a
conflict-free merge, so this fold **never grants a claim** — it only records, per task,
the latest claim a peer's log reports, tagged with the hub that authored it and marked
observed (advisory). A release clears the observed claim. A follower uses this view to
*see* who holds what across hubs; a real claim request is still routed to the namespace's
owning hub, never satisfied from this fold. The function is pure and deterministic: the
same merged log always folds to the same view.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_merge import HubEvent

_CLAIM_KINDS = frozenset({EventKind.CLAIM, EventKind.TASK_UPDATE})
"""Event kinds that assert or refresh a claim on a task (folded as observed, never granted)."""


@dataclass(frozen=True)
class ObservedClaim:
    """A peer's claim on a task as *observed* across hubs — advisory, never a local grant.

    Attributes
    ----------
    task_id : str
        The claimed task id.
    hub_id : str
        Id of the hub whose log authored this observed claim.
    claim : Mapping[str, Any]
        The claim payload (a :meth:`~synapse_channel.core.state_models.TaskClaim.as_dict`).
    """

    task_id: str
    hub_id: str
    claim: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping marked as an observed (non-authoritative) view."""
        return {
            "task_id": self.task_id,
            "hub_id": self.hub_id,
            "observed": True,
            "claim": dict(self.claim),
        }


@dataclass(frozen=True)
class ObservedState:
    """The mergeable coordination state folded from a merged multi-hub log.

    Attributes
    ----------
    board : Mapping[str, Mapping[str, Any]]
        Task id to its latest declared record (last-writer-wins over the merged order).
    progress : tuple[Mapping[str, Any], ...]
        The progress ledger, grow-only and in merged order.
    observed_claims : Mapping[str, ObservedClaim]
        Task id to the latest observed claim; a released task has none. Advisory only —
        this view never grants a claim.
    """

    board: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    progress: tuple[Mapping[str, Any], ...] = ()
    observed_claims: Mapping[str, ObservedClaim] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping of the observed state."""
        return {
            "board": {task_id: dict(record) for task_id, record in self.board.items()},
            "progress": [dict(note) for note in self.progress],
            "observed_claims": {
                task_id: claim.to_dict() for task_id, claim in self.observed_claims.items()
            },
        }


def _task_id_of(event: HubEvent) -> str:
    """Return the stripped ``task_id`` an event carries, or ``""`` when absent."""
    return str(event.payload.get("task_id", "")).strip()


def fold_observed_state(events: Iterable[HubEvent]) -> ObservedState:
    """Fold a merged, ordered multi-hub log into the observed mergeable view.

    Parameters
    ----------
    events : Iterable[HubEvent]
        Hub-tagged events in the deterministic order
        :func:`synapse_channel.core.multihub_merge.merge_event_logs` produces.

    Returns
    -------
    ObservedState
        The board (last-writer-wins per task), the grow-only progress ledger, and the
        observed claim view (latest claim per task, cleared on release). No claim is
        granted; the claim view is advisory.
    """
    board: dict[str, Mapping[str, Any]] = {}
    progress: list[Mapping[str, Any]] = []
    observed_claims: dict[str, ObservedClaim] = {}
    for event in events:
        if event.kind == EventKind.LEDGER_TASK:
            task_id = _task_id_of(event)
            if task_id:
                board[task_id] = dict(event.payload)
        elif event.kind == EventKind.LEDGER_PROGRESS:
            progress.append(dict(event.payload))
        elif event.kind in _CLAIM_KINDS:
            task_id = _task_id_of(event)
            if task_id:
                observed_claims[task_id] = ObservedClaim(
                    task_id=task_id, hub_id=event.hub_id, claim=dict(event.payload)
                )
        elif event.kind == EventKind.RELEASE:
            observed_claims.pop(_task_id_of(event), None)
    return ObservedState(board=board, progress=tuple(progress), observed_claims=observed_claims)
