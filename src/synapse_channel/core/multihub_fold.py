# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fold a merged multi-hub event log into an observed mergeable view
"""Fold a merged multi-hub event log into an *observed* mergeable view.

:mod:`synapse_channel.core.multihub_merge` produces the deterministic union of several
hubs' event logs. This module folds that ordered stream into an observed display view
(`docs/multi-hub-sync.md`): the **board** (display-only last-write-wins per task), the
**progress** ledger (grow-only, every note kept in order), and the **observed claim**
view. Board ordering by ``(timestamp, hub_id, seq)`` is deterministic but is neither
causal nor authoritative; a clock-ahead older declaration can be the displayed winner.
The winning event's provenance and that limitation are exposed with the board.

The claim view is the safety-critical part. Claims are mutual exclusion, not a
conflict-free merge, so this fold **never grants a claim** — it only records, per task,
the latest claim a peer's log reports, tagged with the hub that authored it and marked
observed (advisory). A release clears the observed claim. A follower uses this view to
*see* who holds what across hubs; a real claim request is still routed to the namespace's
owning hub, never satisfied from this fold. The function is pure and deterministic: the
same merged log always folds to the same view.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_merge import HubEvent

_CLAIM_KINDS = frozenset({EventKind.CLAIM, EventKind.TASK_UPDATE})
"""Event kinds that assert or refresh a claim on a task (folded as observed, never granted)."""

BOARD_DISPLAY_WARNING = (
    "Deterministic timestamp ordering is display-only; it is not authoritative task state or "
    "causal proof. Clock synchronization, including NTP, does not establish causality."
)
"""Stable operator warning attached to every observed multi-hub board projection."""


def observed_board_policy() -> dict[str, Any]:
    """Return the explicit contract for the multi-hub board display projection."""
    return {
        "mode": "display-only-lww",
        "order": ["timestamp", "hub_id", "seq"],
        "authoritative": False,
        "causal": False,
        "warning": BOARD_DISPLAY_WARNING,
    }


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
class ObservedBoardProvenance:
    """Event identity and display key for one observed board winner.

    This is provenance for a deterministic presentation choice, not a task version,
    parent relation, vector clock, or authority receipt.
    """

    task_id: str
    hub_id: str
    seq: int
    timestamp: float

    @property
    def order_key(self) -> tuple[float, str, int]:
        """Return the total-order key that selected this displayed record."""
        return (self.timestamp, self.hub_id, self.seq)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible, explicitly non-causal winner provenance."""
        return {
            "task_id": self.task_id,
            "hub_id": self.hub_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "order_key": list(self.order_key),
            "display_winner": True,
            "authoritative": False,
            "causal": False,
        }


@dataclass(frozen=True)
class ObservedState:
    """The mergeable coordination state folded from a merged multi-hub log.

    Attributes
    ----------
    board : Mapping[str, Mapping[str, Any]]
        Task id to its displayed record (last-write-wins over the merged order). This
        is explicitly non-authoritative and non-causal.
    board_provenance : Mapping[str, ObservedBoardProvenance]
        Task id to the event identity and order key that selected the displayed record.
    progress : tuple[Mapping[str, Any], ...]
        The progress ledger, grow-only and in merged order.
    observed_claims : Mapping[str, ObservedClaim]
        Task id to the latest observed claim; a released task has none. Advisory only —
        this view never grants a claim.
    """

    board: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    board_provenance: Mapping[str, ObservedBoardProvenance] = field(default_factory=dict)
    progress: tuple[Mapping[str, Any], ...] = ()
    observed_claims: Mapping[str, ObservedClaim] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping of the observed state."""
        return {
            "board": {task_id: dict(record) for task_id, record in self.board.items()},
            "board_policy": observed_board_policy(),
            "board_provenance": {
                task_id: provenance.to_dict()
                for task_id, provenance in self.board_provenance.items()
            },
            "progress": [dict(note) for note in self.progress],
            "observed_claims": {
                task_id: claim.to_dict() for task_id, claim in self.observed_claims.items()
            },
        }


def asserting_owners(
    state: ObservedState, *, project_of: Callable[[str], str]
) -> dict[str, frozenset[str]]:
    """Derive which hubs are *observed* asserting authority over each namespace.

    A hub that granted a claim is observed acting as the owner of the claim's namespace. This
    folds the observed claim view into that signal: per namespace, the set of hub ids seen
    holding a claim in it. It is the runtime feed
    :meth:`synapse_channel.core.namespace_ownership.NamespaceOwnership.resolve` consumes as
    ``asserting_hubs`` to detect a partition — two hubs both believing they own a namespace —
    and refuse to grant until ownership is re-established.

    The namespace of an observed claim is derived from the claimant exactly as the ownership
    gate derives it, by passing the claim's ``owner`` through ``project_of``; a claim with no
    owner, or one whose owner maps to no namespace, contributes nothing.

    Parameters
    ----------
    state : ObservedState
        The folded multi-hub view whose observed claims are read.
    project_of : Callable[[str], str]
        Maps an agent identity to its namespace, the same derivation the ACL and the ownership
        gate use; injected so this fold stays free of the enforcement layer.

    Returns
    -------
    dict[str, frozenset[str]]
        Namespace to the set of hub ids observed asserting authority over it. A namespace no
        observed claim touches is absent.
    """
    owners: dict[str, set[str]] = {}
    for observed in state.observed_claims.values():
        owner = str(observed.claim.get("owner", "")).strip()
        if not owner:
            continue
        namespace = project_of(owner)
        if not namespace:
            continue
        owners.setdefault(namespace, set()).add(observed.hub_id)
    return {namespace: frozenset(hubs) for namespace, hubs in owners.items()}


def asserting_owners_from_events(
    events: Iterable[HubEvent], *, project_of: Callable[[str], str]
) -> dict[str, frozenset[str]]:
    """Derive asserting hubs without collapsing equal task ids across hubs.

    The general observed-state projection is last-writer-wins by ``task_id`` for
    display.  Partition detection needs a stricter identity: one hub's release
    of task ``T`` must never clear another hub's independently recorded claim on
    its own task ``T``.  This fold therefore keys live claims by
    ``(hub_id, task_id)`` and orders each hub by its authoritative local sequence.
    """
    live: dict[tuple[str, str], Mapping[str, Any]] = {}
    for event in sorted(events, key=lambda item: (item.hub_id, item.seq)):
        task_id = _task_id_of(event)
        if not task_id:
            continue
        identity = (event.hub_id, task_id)
        if event.kind in _CLAIM_KINDS:
            live[identity] = event.payload
        elif event.kind == EventKind.RELEASE:
            live.pop(identity, None)

    owners: dict[str, set[str]] = {}
    for (hub_id, _task_id), claim in live.items():
        owner = str(claim.get("owner", "")).strip()
        namespace = project_of(owner) if owner else ""
        if namespace:
            owners.setdefault(namespace, set()).add(hub_id)
    return {namespace: frozenset(hubs) for namespace, hubs in owners.items()}


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
        The display-only board (last-write-wins per task), its winning-event
        provenance, the grow-only progress ledger, and the observed claim view (latest
        claim per task, cleared on release). The board is non-authoritative and
        non-causal. No claim is granted; the claim view is advisory.
    """
    board: dict[str, Mapping[str, Any]] = {}
    board_provenance: dict[str, ObservedBoardProvenance] = {}
    progress: list[Mapping[str, Any]] = []
    observed_claims: dict[str, ObservedClaim] = {}
    for event in events:
        if event.kind == EventKind.LEDGER_TASK:
            task_id = _task_id_of(event)
            if task_id:
                board[task_id] = dict(event.payload)
                board_provenance[task_id] = ObservedBoardProvenance(
                    task_id=task_id,
                    hub_id=event.hub_id,
                    seq=event.seq,
                    timestamp=event.ts,
                )
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
    return ObservedState(
        board=board,
        board_provenance=board_provenance,
        progress=tuple(progress),
        observed_claims=observed_claims,
    )
