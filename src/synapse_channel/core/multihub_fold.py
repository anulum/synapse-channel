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
(`docs/multi-hub-sync.md`): the **board** (verified causal heads, then display-only
last-write-wins among unresolved heads), payload-free **board conflicts**, the
**progress** ledger (grow-only, every note kept in order), and the **observed claim**
view. Same-hub sequence and an optional content-bound, same-task parent form the only
causal edges. A unique verified head wins even when an ancestor's clock is ahead;
ordering remaining heads by ``(timestamp, hub_id, seq)`` is deterministic but not
causal or authoritative. Provenance, unresolved divergence, and those limits are
exposed with the board.

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
from dataclasses import dataclass, field, replace
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_equivocation import (
    event_fingerprint,
    remember_content_bound_event,
)
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.core.task_causality import (
    TASK_CAUSAL_PARENT_FIELD,
    TaskCausalParent,
    parse_task_causal_parent,
    task_record_payload,
)

_CLAIM_KINDS = frozenset({EventKind.CLAIM, EventKind.TASK_UPDATE})
"""Event kinds that assert or refresh a claim on a task (folded as observed, never granted)."""

BOARD_DISPLAY_WARNING = (
    "Verified content-bound parent edges suppress only proven ancestors; remaining timestamp "
    "ordering is display-only and not authoritative task state or proof of concurrency. Clock "
    "synchronization, including NTP, does not establish causality."
)
"""Stable operator warning attached to every observed multi-hub board projection."""


def observed_board_policy() -> dict[str, Any]:
    """Return the explicit contract for the multi-hub board display projection."""
    return {
        "mode": "causal-head-then-display-lww",
        "causal_parent": "content-bound-single-parent",
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

    This is provenance for a deterministic presentation choice. ``causal`` is true
    only when verified cross-hub ancestry selected a unique head; it is never an
    authority receipt or a vector clock.
    """

    task_id: str
    hub_id: str
    seq: int
    timestamp: float
    event_fingerprint: str = ""
    selection: str = "display-order"
    causal: bool = False
    causal_parent: TaskCausalParent | None = None
    causal_parent_status: str = "none"

    @property
    def order_key(self) -> tuple[float, str, int]:
        """Return the total-order key that selected this displayed record."""
        return (self.timestamp, self.hub_id, self.seq)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible, explicitly non-authoritative winner provenance."""
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "hub_id": self.hub_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "order_key": list(self.order_key),
            "event_fingerprint": self.event_fingerprint,
            "selection": self.selection,
            "display_winner": True,
            "authoritative": False,
            "causal": self.causal,
        }
        if self.causal_parent is not None:
            payload["causal_parent"] = self.causal_parent.to_dict()
        if self.causal_parent_status != "none":
            payload["causal_parent_status"] = self.causal_parent_status
        return payload


@dataclass(frozen=True)
class ObservedBoardContender:
    """Payload-free evidence for one hub's latest snapshot of an observed task.

    Attributes
    ----------
    hub_id : str
        Hub that authored the snapshot.
    seq : int
        Authoring hub's local event sequence.
    timestamp : float
        Wall-clock timestamp carried by the event; display metadata only.
    record_fingerprint : str
        Domain-separated SHA-256 fingerprint of the complete task record.
    """

    hub_id: str
    seq: int
    timestamp: float
    record_fingerprint: str
    event_fingerprint: str = ""
    causal_parent: TaskCausalParent | None = None
    causal_parent_status: str = "none"

    def to_dict(self) -> dict[str, Any]:
        """Return bounded contender evidence without the task payload."""
        payload: dict[str, Any] = {
            "hub_id": self.hub_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "record_fingerprint": self.record_fingerprint,
        }
        if self.event_fingerprint:
            payload["event_fingerprint"] = self.event_fingerprint
        if self.causal_parent is not None:
            payload["causal_parent"] = self.causal_parent.to_dict()
        if self.causal_parent_status != "none":
            payload["causal_parent_status"] = self.causal_parent_status
        return payload


@dataclass(frozen=True)
class ObservedBoardConflict:
    """Unresolved divergence between independently authored task snapshots.

    This object proves that remaining observed heads from at least two hubs differ.
    Verified single-parent edges remove ancestors, but missing edges do not prove
    that the remaining heads were concurrent. The display winner remains separate
    and non-authoritative.

    Attributes
    ----------
    task_id : str
        Task whose latest per-hub snapshots diverge.
    contenders : tuple[ObservedBoardContender, ...]
        Deterministically ordered, payload-free latest snapshot evidence.
    """

    task_id: str
    contenders: tuple[ObservedBoardContender, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible unresolved conflict record."""
        return {
            "task_id": self.task_id,
            "status": "unresolved",
            "kind": "cross-hub-snapshot-divergence",
            "causal": False,
            "causal_parent_aware": True,
            "resolution": "operator-required",
            "contenders": [contender.to_dict() for contender in self.contenders],
        }


@dataclass(frozen=True)
class ObservedState:
    """The mergeable coordination state folded from a merged multi-hub log.

    Attributes
    ----------
    board : Mapping[str, Mapping[str, Any]]
        Task id to its displayed record (a unique verified causal head when present,
        otherwise last-write-wins among unresolved heads). Non-authoritative.
    board_provenance : Mapping[str, ObservedBoardProvenance]
        Task id to the event identity and order key that selected the displayed record.
    progress : tuple[Mapping[str, Any], ...]
        The progress ledger, grow-only and in merged order.
    observed_claims : Mapping[str, ObservedClaim]
        Task id to the latest observed claim; a released task has none. Advisory only —
        this view never grants a claim.
    board_conflicts : Mapping[str, ObservedBoardConflict]
        Task id to unresolved, payload-free cross-hub snapshot divergence. These
        records do not infer concurrency from missing edges or select authority.
    """

    board: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    board_provenance: Mapping[str, ObservedBoardProvenance] = field(default_factory=dict)
    progress: tuple[Mapping[str, Any], ...] = ()
    observed_claims: Mapping[str, ObservedClaim] = field(default_factory=dict)
    board_conflicts: Mapping[str, ObservedBoardConflict] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping of the observed state."""
        return {
            "board": {task_id: dict(record) for task_id, record in self.board.items()},
            "board_policy": observed_board_policy(),
            "board_provenance": {
                task_id: provenance.to_dict()
                for task_id, provenance in self.board_provenance.items()
            },
            "board_conflicts": {
                task_id: conflict.to_dict() for task_id, conflict in self.board_conflicts.items()
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


def _board_contender(event: HubEvent) -> ObservedBoardContender:
    """Return bounded task-record evidence using the canonical event encoder."""
    fingerprint_event = HubEvent(
        hub_id="synapse-board-record",
        seq=1,
        ts=0.0,
        kind=EventKind.LEDGER_TASK,
        payload=task_record_payload(event.payload),
    )
    parent: TaskCausalParent | None = None
    parent_status = "none"
    if TASK_CAUSAL_PARENT_FIELD in event.payload:
        try:
            parent = parse_task_causal_parent(event.payload[TASK_CAUSAL_PARENT_FIELD])
            parent_status = "unverified"
        except ValueError:
            parent_status = "invalid"
    return ObservedBoardContender(
        hub_id=event.hub_id,
        seq=event.seq,
        timestamp=event.ts,
        record_fingerprint=event_fingerprint(fingerprint_event),
        event_fingerprint=event_fingerprint(event),
        causal_parent=parent,
        causal_parent_status=parent_status,
    )


def _board_conflicts(
    contenders: Mapping[str, tuple[ObservedBoardContender, ...]],
) -> dict[str, ObservedBoardConflict]:
    """Return divergent causal heads without asserting that they are concurrent."""
    conflicts: dict[str, ObservedBoardConflict] = {}
    for task_id, task_contenders in contenders.items():
        ordered = tuple(sorted(task_contenders, key=lambda item: (item.hub_id, item.seq)))
        if len(ordered) > 1 and len({item.record_fingerprint for item in ordered}) > 1:
            conflicts[task_id] = ObservedBoardConflict(task_id=task_id, contenders=ordered)
    return conflicts


def _verified_task_heads(
    events: tuple[HubEvent, ...],
    *,
    all_task_events: Mapping[tuple[str, int], HubEvent],
) -> tuple[tuple[HubEvent, ...], dict[tuple[str, int], ObservedBoardContender]]:
    """Return maximal task events after verifying explicit and implicit ancestry."""
    contenders = {event.identity: _board_contender(event) for event in events}
    parents: dict[tuple[str, int], set[tuple[str, int]]] = {
        event.identity: set() for event in events
    }

    by_hub: dict[str, list[HubEvent]] = {}
    for event in events:
        by_hub.setdefault(event.hub_id, []).append(event)
    for local_events in by_hub.values():
        ordered = sorted(local_events, key=lambda item: item.seq)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            parents[current.identity].add(previous.identity)

    for event in events:
        contender = contenders[event.identity]
        parent = contender.causal_parent
        if parent is None:
            continue
        target = all_task_events.get(parent.identity)
        status = "missing"
        if target is not None:
            if _task_id_of(target) != _task_id_of(event):
                status = "wrong-task"
            elif event_fingerprint(target) != parent.event_fingerprint:
                status = "fingerprint-mismatch"
            elif target.identity == event.identity:
                status = "self-parent"
            else:
                status = "verified"
                parents[event.identity].add(target.identity)
        contenders[event.identity] = ObservedBoardContender(
            hub_id=contender.hub_id,
            seq=contender.seq,
            timestamp=contender.timestamp,
            record_fingerprint=contender.record_fingerprint,
            event_fingerprint=contender.event_fingerprint,
            causal_parent=parent,
            causal_parent_status=status,
        )

    if _parent_graph_has_cycle(parents):
        parents = {event.identity: set() for event in events}
        for local_events in by_hub.values():
            ordered = sorted(local_events, key=lambda item: item.seq)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                parents[current.identity].add(previous.identity)
        contenders = {
            identity: (
                replace(contender, causal_parent_status="cycle-rejected")
                if contender.causal_parent_status == "verified"
                else contender
            )
            for identity, contender in contenders.items()
        }

    referenced = {parent for task_parents in parents.values() for parent in task_parents}
    heads = [event for event in events if event.identity not in referenced]
    return tuple(heads), contenders


def _parent_graph_has_cycle(
    parents: Mapping[tuple[str, int], set[tuple[str, int]]],
) -> bool:
    """Detect a parent cycle in linear time without recursion-depth exposure."""
    inbound = {identity: 0 for identity in parents}
    children: dict[tuple[str, int], set[tuple[str, int]]] = {
        identity: set() for identity in parents
    }
    for child, task_parents in parents.items():
        for parent in task_parents:
            if parent not in inbound:
                continue
            inbound[parent] += 1
            children[child].add(parent)
    ready = [identity for identity, count in inbound.items() if count == 0]
    consumed = 0
    while ready:
        identity = ready.pop()
        consumed += 1
        for parent in children[identity]:
            inbound[parent] -= 1
            if inbound[parent] == 0:
                ready.append(parent)
    return consumed != len(inbound)


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
        The non-authoritative board (a verified causal head when unique, then
        last-write-wins among unresolved heads), its winning-event provenance,
        payload-free unresolved divergence records, the grow-only progress
        ledger, and the observed claim view (latest claim per task, cleared on
        release). No claim is granted; the claim view is advisory.
    """
    board: dict[str, Mapping[str, Any]] = {}
    board_provenance: dict[str, ObservedBoardProvenance] = {}
    board_events: dict[str, list[HubEvent]] = {}
    content_bound_task_events: dict[tuple[str, int], HubEvent] = {}
    progress: list[Mapping[str, Any]] = []
    observed_claims: dict[str, ObservedClaim] = {}
    for event in events:
        if event.kind == EventKind.LEDGER_TASK:
            task_id = _task_id_of(event)
            if task_id:
                before = len(content_bound_task_events)
                remember_content_bound_event(content_bound_task_events, event)
                if len(content_bound_task_events) != before:
                    board_events.setdefault(task_id, []).append(event)
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

    board_heads: dict[str, tuple[ObservedBoardContender, ...]] = {}
    all_task_events = content_bound_task_events
    for task_id, task_events in board_events.items():
        heads, contenders = _verified_task_heads(
            tuple(task_events), all_task_events=all_task_events
        )
        if not heads:
            heads = tuple(task_events)
        winner = max(heads, key=lambda item: item.order_key)
        winner_contender = contenders[winner.identity]
        head_identities = {event.identity for event in heads}
        causal_selection = len(heads) == 1 and any(
            event.hub_id != winner.hub_id and event.identity not in head_identities
            for event in task_events
        )
        board[task_id] = task_record_payload(winner.payload)
        board_provenance[task_id] = ObservedBoardProvenance(
            task_id=task_id,
            hub_id=winner.hub_id,
            seq=winner.seq,
            timestamp=winner.ts,
            event_fingerprint=winner_contender.event_fingerprint,
            selection="causal-head" if causal_selection else "display-order",
            causal=causal_selection,
            causal_parent=winner_contender.causal_parent,
            causal_parent_status=winner_contender.causal_parent_status,
        )
        board_heads[task_id] = tuple(contenders[event.identity] for event in heads)
    return ObservedState(
        board=board,
        board_provenance=board_provenance,
        board_conflicts=_board_conflicts(board_heads),
        progress=tuple(progress),
        observed_claims=observed_claims,
    )
