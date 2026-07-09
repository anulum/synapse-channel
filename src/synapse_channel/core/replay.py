# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reconstruct a task's state at a sequence and plan a fork
"""Reconstruct a task's authoritative state from the durable event log.

The hub is event-sourced: every authoritative claim mutation is appended to the
log as a snapshot (see :class:`~synapse_channel.core.journal.EventKind`). This
module folds that log back into the exact claim state a task held *as of* any
sequence point, and plans a **fork** from there — the resume manifest an agent
would pick up if the task were rewound to that point, with chosen fields
overridden.

It is read-only inspection over the log, not execution. The hub never runs a
task; agents do. So a fork is a *what-if manifest*, not a re-run: it shows the
reconstructed checkpoint state, applies the requested overrides, and reports how
the real history diverged after the fork point. It does not contact a live hub
and it changes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import EventStore, StoredEvent

SNAPSHOT_KINDS = frozenset(
    {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}
)
"""Event kinds whose payload is a full task-claim snapshot."""

DIVERGENCE_KINDS = SNAPSHOT_KINDS | {EventKind.RELEASE, EventKind.LEDGER_PROGRESS}
"""Task-scoped event kinds reported on the post-fork divergence path."""

OVERRIDABLE_FIELDS = frozenset({"owner", "note", "status", "data_ref", "worktree", "checkpoint"})
"""Claim fields a fork manifest may override.

Deliberately excludes ``paths`` (a list, not a scalar), the concurrency
internals ``epoch``/``version``, the clock-derived ``claimed_at``/
``lease_expires_at``, and the opaque ``git`` context: a fork is a what-if over
resumable intent, not a rewrite of lease bookkeeping.
"""


@dataclass(frozen=True)
class ReconstructedClaim:
    """The authoritative claim state of one task folded to a sequence point.

    Attributes
    ----------
    task_id : str
        Identifier of the reconstructed task.
    owner : str
        Agent holding the claim at the fold point.
    status : str
        Lifecycle status the snapshot carried.
    note : str
        Free-form context carried by the snapshot.
    checkpoint : str
        Opaque resume token saved with the claim — the state an agent resumes
        from.
    data_ref : str
        Optional pointer to produced artefacts.
    worktree : str
        Worktree label the work happens in.
    paths : tuple[str, ...]
        Declared file/directory scopes; empty means the whole worktree.
    epoch : int
        Lease generation observed at the fold point.
    version : int
        Optimistic-concurrency counter observed at the fold point.
    source_seq : int
        Sequence of the snapshot event that produced this state.
    source_kind : str
        Event kind of that snapshot.
    source_ts : float
        Timestamp of that snapshot.
    payload : dict[str, Any]
        The raw winning snapshot payload, preserved verbatim so a fork manifest
        and a state digest see every field (including ones not projected above).
    """

    task_id: str
    owner: str
    status: str
    note: str
    checkpoint: str
    data_ref: str
    worktree: str
    paths: tuple[str, ...]
    epoch: int
    version: int
    source_seq: int
    source_kind: str
    source_ts: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class DivergedEvent:
    """One task event that occurred after the fork point — the real history.

    Attributes
    ----------
    seq : int
        Durable event-log sequence.
    ts : float
        Event timestamp.
    kind : str
        Durable event kind.
    status : str
        Task/claim status when the event carries one.
    actor : str
        Best-effort actor field (owner or author).
    text : str
        Short human-readable payload summary.
    """

    seq: int
    ts: float
    kind: str
    status: str
    actor: str
    text: str


@dataclass(frozen=True)
class ForkPlan:
    """A what-if rewind of a task to a sequence point.

    Attributes
    ----------
    task_id : str
        Task the fork applies to.
    fork_seq : int
        Inclusive sequence the task is rewound to.
    held : bool
        Whether the task held a live claim at ``fork_seq``. When ``False`` there
        is nothing to resume (the task was released or never claimed by then) and
        ``base``/``resume`` are empty.
    base : ReconstructedClaim or None
        The reconstructed claim state at ``fork_seq``; ``None`` when not held.
    overrides : tuple[tuple[str, str], ...]
        The field overrides applied to the resume manifest, in sorted order.
    resume : dict[str, Any]
        The fork's resume manifest: the base snapshot payload with overrides
        applied. Empty when not held.
    diverged : tuple[DivergedEvent, ...]
        Task events that really happened after ``fork_seq`` — what the fork
        diverges from.
    generated_from_seq : int
        Highest event sequence considered.
    """

    task_id: str
    fork_seq: int
    held: bool
    base: ReconstructedClaim | None
    overrides: tuple[tuple[str, str], ...]
    resume: dict[str, Any]
    diverged: tuple[DivergedEvent, ...]
    generated_from_seq: int


def reconstruct_claim(
    task_id: str,
    events: Sequence[StoredEvent],
    *,
    as_of_seq: int | None = None,
) -> ReconstructedClaim | None:
    """Fold the log into a task's claim state at a sequence point.

    Walks the task's snapshot and release events in order; the last snapshot wins
    and a release clears the claim. This mirrors the hub's own start-up replay
    (:mod:`synapse_channel.core.journal`) restricted to one task.

    Parameters
    ----------
    task_id : str
        Task to reconstruct.
    events : Sequence[StoredEvent]
        Events to fold, in any order (filtered and ordered internally).
    as_of_seq : int or None, optional
        Inclusive upper bound; only events with ``seq <= as_of_seq`` are folded.
        ``None`` folds the whole log (the task's latest state).

    Returns
    -------
    ReconstructedClaim or None
        The claim state at the fold point, or ``None`` when the task held no live
        claim there (released or never claimed).
    """
    clean = task_id.strip()
    winner: StoredEvent | None = None
    for event in sorted(events, key=lambda item: item.seq):
        if as_of_seq is not None and event.seq > as_of_seq:
            break
        if _event_task_id(event) != clean:
            continue
        if event.kind == EventKind.RELEASE:
            winner = None
        elif event.kind in SNAPSHOT_KINDS:
            winner = event
    if winner is None:
        return None
    return _claim_from_event(winner)


def build_fork_plan(
    task_id: str,
    events: Sequence[StoredEvent],
    *,
    fork_seq: int,
    overrides: Mapping[str, str],
) -> ForkPlan:
    """Plan a what-if fork of a task rewound to ``fork_seq``.

    Parameters
    ----------
    task_id : str
        Task to fork.
    events : Sequence[StoredEvent]
        Loaded events.
    fork_seq : int
        Inclusive sequence to rewind to; must be non-negative.
    overrides : Mapping[str, str]
        Field overrides for the resume manifest; keys must be a subset of
        :data:`OVERRIDABLE_FIELDS`.

    Returns
    -------
    ForkPlan
        The reconstructed base, the resume manifest, and the post-fork
        divergence.

    Raises
    ------
    ValueError
        If ``fork_seq`` is negative or an override key is not overridable.
    """
    if fork_seq < 0:
        msg = f"fork_seq must be non-negative, got {fork_seq}"
        raise ValueError(msg)
    forbidden = sorted(set(overrides) - OVERRIDABLE_FIELDS)
    if forbidden:
        allowed = ", ".join(sorted(OVERRIDABLE_FIELDS))
        msg = f"cannot override {', '.join(forbidden)}; overridable fields: {allowed}"
        raise ValueError(msg)
    clean = task_id.strip()
    base = reconstruct_claim(clean, events, as_of_seq=fork_seq)
    sorted_overrides = tuple(sorted(overrides.items()))
    if base is None:
        resume: dict[str, Any] = {}
        held = False
    else:
        resume = {**base.payload, **dict(sorted_overrides)}
        held = True
    diverged = tuple(
        _diverged_event(event)
        for event in sorted(events, key=lambda item: item.seq)
        if event.seq > fork_seq
        and event.kind in DIVERGENCE_KINDS
        and _event_task_id(event) == clean
    )
    return ForkPlan(
        task_id=clean,
        fork_seq=fork_seq,
        held=held,
        base=base,
        overrides=sorted_overrides,
        resume=resume,
        diverged=diverged,
        generated_from_seq=max((event.seq for event in events), default=0),
    )


def infer_task_at_seq(events: Sequence[StoredEvent], seq: int) -> str | None:
    """Return the task id carried by the event at ``seq``, if any.

    Lets ``--fork-at`` stand alone: the task is taken from the snapshot or
    release sitting at that sequence.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events.
    seq : int
        Exact sequence to look up.

    Returns
    -------
    str or None
        The task id at that sequence, or ``None`` when no event sits there or it
        carries no task id.
    """
    for event in events:
        if event.seq == seq:
            task = _event_task_id(event)
            return task or None
    return None


def run_fork(
    db_path: str | Path,
    task_id: str,
    *,
    fork_seq: int,
    overrides: Mapping[str, str],
    key_file: str | Path | None = None,
) -> ForkPlan:
    """Build a fork plan from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    task_id : str
        Task id to fork.
    fork_seq : int
        Inclusive sequence to rewind to.
    overrides : Mapping[str, str]
        Resume-manifest field overrides.

    Returns
    -------
    ForkPlan
        The plan built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist, ``fork_seq`` is negative, or an
        override key is not overridable.
    """
    events = _load_events(db_path, key_file=key_file)
    return build_fork_plan(task_id, events, fork_seq=fork_seq, overrides=overrides)


def load_task_for_seq(
    db_path: str | Path,
    seq: int,
    *,
    key_file: str | Path | None = None,
) -> str | None:
    """Load the store and return the task id at ``seq``, if any.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    seq : int
        Exact sequence to look up.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted event store.

    Returns
    -------
    str or None
        The inferred task id, or ``None``.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return infer_task_at_seq(_load_events(db_path, key_file=key_file), seq)


def fork_plan_to_json(plan: ForkPlan) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a fork plan."""
    return {
        "task_id": plan.task_id,
        "fork_seq": plan.fork_seq,
        "held": plan.held,
        "generated_from_seq": plan.generated_from_seq,
        "base": _claim_to_json(plan.base),
        "overrides": [{"field": field, "value": value} for field, value in plan.overrides],
        "resume": plan.resume,
        "diverged": [_diverged_to_json(event) for event in plan.diverged],
    }


def render_markdown(plan: ForkPlan) -> str:
    """Render a fork plan as compact Markdown for inspection or handovers."""
    base = plan.base
    lines = [
        f"# Fork: {plan.task_id} @ seq {plan.fork_seq}",
        "",
        f"- Generated from event seq: {plan.generated_from_seq}",
        f"- Held at fork point: {'yes' if plan.held else 'no'}",
    ]
    if base is None:
        lines.append("")
        lines.append(f"No live claim for {plan.task_id} at seq {plan.fork_seq}; nothing to fork.")
        return "\n".join(lines)
    lines.extend(
        [
            f"- Resumed from snapshot: seq={base.source_seq} kind={base.source_kind}",
            "",
            "## Resume manifest",
            f"- owner: {plan.resume.get('owner', '')}",
            f"- status: {plan.resume.get('status', '')}",
            f"- checkpoint: {plan.resume.get('checkpoint', '')}",
            f"- note: {plan.resume.get('note', '')}",
        ]
    )
    if plan.overrides:
        applied = ", ".join(f"{field}={value}" for field, value in plan.overrides)
        lines.append(f"- overrides applied: {applied}")
    lines.append("")
    lines.append("## Diverged after fork")
    if plan.diverged:
        lines.extend(_render_diverged(event) for event in plan.diverged)
    else:
        lines.append("- none")
    return "\n".join(lines)


def _load_events(
    db_path: str | Path,
    *,
    key_file: str | Path | None = None,
) -> tuple[StoredEvent, ...]:
    """Load every event from a store, raising on a missing file."""
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        return tuple(store.read_all())
    finally:
        store.close()


def _claim_from_event(event: StoredEvent) -> ReconstructedClaim:
    """Project a winning snapshot event into a reconstructed claim."""
    payload = event.payload
    return ReconstructedClaim(
        task_id=str(payload.get("task_id", "")),
        owner=str(payload.get("owner", "")),
        status=str(payload.get("status", "")),
        note=str(payload.get("note", "")),
        checkpoint=str(payload.get("checkpoint", "")),
        data_ref=str(payload.get("data_ref", "")),
        worktree=str(payload.get("worktree", "")),
        paths=tuple(str(path) for path in payload.get("paths", ())),
        epoch=safe_int(payload.get("epoch", 0), default=0),
        version=safe_int(payload.get("version", 0), default=0),
        source_seq=event.seq,
        source_kind=event.kind,
        source_ts=event.ts,
        payload=payload,
    )


def _diverged_event(event: StoredEvent) -> DivergedEvent:
    """Project a post-fork task event into a divergence entry."""
    payload = event.payload
    return DivergedEvent(
        seq=event.seq,
        ts=event.ts,
        kind=event.kind,
        status=str(payload.get("status", "")),
        actor=_actor(payload),
        text=_text(payload),
    )


def _actor(payload: Mapping[str, Any]) -> str:
    """Return the best actor field a divergence payload carries.

    A snapshot carries ``owner`` and a progress note carries ``author``; a bare
    release carries neither, which projects to an empty actor.
    """
    for key in ("owner", "author"):
        value = str(payload.get(key, ""))
        if value:
            return value
    return ""


def _text(payload: Mapping[str, Any]) -> str:
    """Return the most useful text field carried by a payload."""
    for key in ("text", "note", "data_ref"):
        value = str(payload.get(key, ""))
        if value:
            return value
    return ""


def _event_task_id(event: StoredEvent) -> str:
    """Return an event payload's task id."""
    return str(event.payload.get("task_id", ""))


def _claim_to_json(claim: ReconstructedClaim | None) -> dict[str, object] | None:
    """Convert a reconstructed claim into JSON-compatible fields."""
    if claim is None:
        return None
    return {
        "task_id": claim.task_id,
        "owner": claim.owner,
        "status": claim.status,
        "note": claim.note,
        "checkpoint": claim.checkpoint,
        "data_ref": claim.data_ref,
        "worktree": claim.worktree,
        "paths": list(claim.paths),
        "epoch": claim.epoch,
        "version": claim.version,
        "source_seq": claim.source_seq,
        "source_kind": claim.source_kind,
        "source_ts": claim.source_ts,
    }


def _diverged_to_json(event: DivergedEvent) -> dict[str, object]:
    """Convert a divergence entry into JSON-compatible fields."""
    return {
        "seq": event.seq,
        "ts": event.ts,
        "kind": event.kind,
        "status": event.status,
        "actor": event.actor,
        "text": event.text,
    }


def _render_diverged(event: DivergedEvent) -> str:
    """Render one divergence entry."""
    status = f" status={event.status}" if event.status else ""
    actor = f" actor={event.actor}" if event.actor else ""
    text = f" — {event.text}" if event.text else ""
    return f"- seq={event.seq} ts={event.ts:.3f} kind={event.kind}{status}{actor}{text}"
