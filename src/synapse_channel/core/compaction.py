# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded retention for the durable event log
"""Bound the durable write log without breaking replay or the ingest seam.

The coordination kinds in the event log are self-trimming — a release removes a
claim, a later snapshot supersedes an earlier one — but the two memory kinds the
hub commits at full durability grow without bound: every resume
:data:`~synapse_channel.core.journal.EventKind.CHECKPOINT` and every authored
:data:`~synapse_channel.core.journal.EventKind.FINDING` is kept forever. This
module is the opt-in knob that bounds them, and it is written to respect the two
readers of the log at once:

* **Coordination replay** (:func:`~synapse_channel.core.journal.replay`)
  reconstructs each claim from the *latest* claim-snapshot event for its task, so
  a checkpoint may be dropped only when a newer checkpoint for the same task
  survives. Keeping the latest *N* checkpoints per task (``N >= 1``) leaves the
  newest snapshot in place, so replay is unchanged. Findings are skipped by
  replay entirely, so removing them never touches coordination state.
* **The persistent-memory read-side** polls
  :meth:`~synapse_channel.core.persistence.EventStore.read_since` with a sequence
  cursor and is promised loss-free, incremental delivery. Compaction therefore
  never deletes an event above a caller-supplied ``floor_seq``: the floor is the
  lowest sequence every consumer has already ingested, so nothing unconsumed is
  removed. Deleting a row never recycles its sequence (the primary key only ever
  increases), so a cursor simply skips the gap.

The policy is declarative (:class:`RetentionPolicy`) and the sweep
(:func:`compact`) reports exactly what it removed (:class:`CompactionResult`).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


@dataclass(frozen=True)
class RetentionPolicy:
    """How much of the durable memory log to keep.

    The two knobs are independent and additive — a sweep applies whichever are
    set. A policy with neither set is a no-op (:attr:`is_noop`).

    Attributes
    ----------
    max_checkpoints_per_task : int or None
        Keep only the latest this-many resume checkpoints per task; older
        checkpoints below the floor are removed. ``None`` keeps every checkpoint.
        Must be at least ``1`` when set — dropping the newest checkpoint could
        lose the only surviving snapshot of a claim and so corrupt replay.
    finding_grace_seconds : float or None
        Remove a finding whose validity window closed (``validity.valid_to`` set)
        more than this many seconds ago. ``None`` keeps every finding; a finding
        with an open window (``valid_to`` unset) is never aged out, whatever the
        grace.
    """

    max_checkpoints_per_task: int | None = None
    finding_grace_seconds: float | None = None

    def __post_init__(self) -> None:
        """Reject a policy that would corrupt replay or invert time."""
        if self.max_checkpoints_per_task is not None and self.max_checkpoints_per_task < 1:
            raise ValueError(
                "max_checkpoints_per_task must be at least 1: keeping zero would drop the "
                "latest checkpoint, which can be a claim's only surviving snapshot"
            )
        if self.finding_grace_seconds is not None and self.finding_grace_seconds < 0:
            raise ValueError("finding_grace_seconds must not be negative")

    @property
    def is_noop(self) -> bool:
        """Return ``True`` when neither retention knob is set, so a sweep does nothing."""
        return self.max_checkpoints_per_task is None and self.finding_grace_seconds is None


@dataclass(frozen=True)
class CompactionResult:
    """What one :func:`compact` sweep removed.

    Attributes
    ----------
    checkpoints_removed : int
        Number of superseded checkpoint events deleted.
    findings_removed : int
        Number of expired finding events deleted.
    floor_seq : int
        The floor the sweep honoured; no event above it was considered.
    """

    checkpoints_removed: int
    findings_removed: int
    floor_seq: int

    @property
    def total_removed(self) -> int:
        """Return the total number of events the sweep deleted."""
        return self.checkpoints_removed + self.findings_removed


def _superseded_checkpoints(store: EventStore, *, floor: int, keep: int) -> list[int]:
    """Return the sequences of checkpoints below the floor beyond the latest ``keep`` per task.

    Checkpoints above the floor are left in place (and not counted toward
    ``keep``), so the policy bounds the settled prefix while never touching the
    unconsumed tail. The newest checkpoints per task always survive, which is what
    keeps coordination replay exact.
    """
    by_task: dict[str, list[int]] = defaultdict(list)
    for event in store.read_since(0, kinds=(EventKind.CHECKPOINT,)):
        if event.seq > floor:
            continue
        by_task[str(event.payload.get("task_id", ""))].append(event.seq)
    doomed: list[int] = []
    for seqs in by_task.values():
        seqs.sort()  # ascending — the newest sit at the tail
        if len(seqs) > keep:
            doomed.extend(seqs[:-keep])
    return doomed


def _valid_to(payload: dict[str, object]) -> float | None:
    """Return a finding's ``validity.valid_to`` as a float, or ``None`` when open/absent."""
    validity = payload.get("validity")
    if not isinstance(validity, dict):
        return None
    raw = validity.get("valid_to")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _expired_findings(store: EventStore, *, floor: int, cutoff: float) -> list[int]:
    """Return findings below the floor whose validity window closed at or before ``cutoff``."""
    doomed: list[int] = []
    for event in store.read_since(0, kinds=(EventKind.FINDING,)):
        if event.seq > floor:
            continue
        valid_to = _valid_to(event.payload)
        if valid_to is not None and valid_to <= cutoff:
            doomed.append(event.seq)
    return doomed


def compact(
    store: EventStore,
    policy: RetentionPolicy,
    *,
    floor_seq: int,
    now: float | None = None,
) -> CompactionResult:
    """Apply a retention policy to the durable log, deleting only below the floor.

    Parameters
    ----------
    store : EventStore
        The event log to compact in place.
    policy : RetentionPolicy
        Which retention knobs to apply; a no-op policy deletes nothing.
    floor_seq : int
        Compaction only considers events with ``seq <= floor_seq``; nothing above
        it is touched, so a downstream ingest cursor at or below the floor never
        loses an unconsumed event. Pass the lowest sequence every memory consumer
        has ingested (e.g. via :meth:`~synapse_channel.core.persistence.EventStore.max_seq`
        for a fully settled log).
    now : float or None, optional
        Wall-clock time used to age out findings; the system clock is used when
        ``None``.

    Returns
    -------
    CompactionResult
        Counts of what was removed and the floor that was honoured.
    """
    floor = int(floor_seq)
    if policy.is_noop:
        return CompactionResult(checkpoints_removed=0, findings_removed=0, floor_seq=floor)
    ts = time.time() if now is None else float(now)

    doomed_checkpoints: list[int] = []
    if policy.max_checkpoints_per_task is not None:
        doomed_checkpoints = _superseded_checkpoints(
            store, floor=floor, keep=policy.max_checkpoints_per_task
        )
    doomed_findings: list[int] = []
    if policy.finding_grace_seconds is not None:
        doomed_findings = _expired_findings(
            store, floor=floor, cutoff=ts - policy.finding_grace_seconds
        )

    seqs = doomed_checkpoints + doomed_findings
    if seqs:
        store.delete(seqs)
    return CompactionResult(
        checkpoints_removed=len(doomed_checkpoints),
        findings_removed=len(doomed_findings),
        floor_seq=floor,
    )
