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

* **Coordination replay** (:func:`~synapse_channel.core.journal_replay.replay`)
  reconstructs each claim from the *latest* claim-snapshot event for its task, so
  a checkpoint may be dropped only when a newer checkpoint for the same task
  survives. Keeping the latest *N* checkpoints per task (``N >= 1``) leaves the
  newest snapshot in place, so replay is unchanged. Findings are skipped by
  replay entirely, so removing them never touches coordination state.
* **The persistent-memory read-side** polls
  :meth:`~synapse_channel.core.persistence.EventStore.read_since` with a sequence
  cursor. Compaction never deletes an event above a caller-supplied ``floor_seq``,
  and never above the acknowledgement of a registered feed consumer
  (:mod:`~synapse_channel.core.feed_retention`): the floor it applies is the lower
  of the two, unless ``past_feed_acks`` is set or a consumer is released by
  name, and then the consumers it passes are journalled. A consumer that reads
  only sequence cursors and never acknowledges is not registered, so the caller
  still chooses a floor no higher than its cursor. Deleting a row never recycles
  its sequence (the primary key only ever increases), so a cursor skips the gap;
  the feed reports the floor applied.

The policy is declarative (:class:`RetentionPolicy`) and the sweep
(:func:`compact`) reports exactly what it removed (:class:`CompactionResult`).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass

from synapse_channel.core.feed_retention import FeedAckFloor, feed_ack_floor
from synapse_channel.core.journal import EventKind, record_compaction_floor
from synapse_channel.core.persistence import EventStore


class FeedConsumerUnknown(ValueError):
    """Raised when a consumer released by name holds no acknowledgement in the journal."""


@dataclass(frozen=True)
class RetentionPolicy:
    """How much of the durable memory log to keep.

    The three knobs are independent and additive — a sweep applies whichever are
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
    drop_corrupt_rows : bool
        Explicitly delete quarantined rows at or below the settled floor. The CLI
        requires an archive report for this destructive recovery operation.
    """

    max_checkpoints_per_task: int | None = None
    finding_grace_seconds: float | None = None
    drop_corrupt_rows: bool = False

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
        return (
            self.max_checkpoints_per_task is None
            and self.finding_grace_seconds is None
            and not self.drop_corrupt_rows
        )


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
    corrupt_rows_removed : int
        Number of explicitly quarantined rows deleted.
    requested_floor_seq : int or None
        The floor the caller asked for; ``None`` when it equals ``floor_seq``.
    held_by_feed_acks : tuple of (str, int)
        Registered feed consumers whose acknowledgement lies below the requested
        floor; the lowest of them is ``floor_seq``.
    passed_feed_acks : tuple of (str, int)
        Registered feed consumers an explicit override or release compacted past.
    """

    checkpoints_removed: int
    findings_removed: int
    floor_seq: int
    corrupt_rows_removed: int = 0
    requested_floor_seq: int | None = None
    held_by_feed_acks: tuple[tuple[str, int], ...] = ()
    passed_feed_acks: tuple[tuple[str, int], ...] = ()

    @property
    def total_removed(self) -> int:
        """Return the total number of events the sweep deleted."""
        return self.checkpoints_removed + self.findings_removed + self.corrupt_rows_removed


def _superseded_checkpoints(store: EventStore, *, floor: int, keep: int) -> list[int]:
    """Return the sequences of checkpoints below the floor beyond the latest ``keep`` per task.

    Checkpoints above the floor are left in place (and not counted toward
    ``keep``), so the policy bounds the settled prefix while never touching the
    unconsumed tail. The newest checkpoints per task always survive, which is what
    keeps coordination replay exact.
    """
    by_task: dict[str, list[int]] = defaultdict(list)
    # The store filters by kind, so every event here is a checkpoint.
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
    # The store filters by kind, so every event here is a finding.
    for event in store.read_since(0, kinds=(EventKind.FINDING,)):
        if event.seq > floor:
            continue
        valid_to = _valid_to(event.payload)
        if valid_to is not None and valid_to <= cutoff:
            doomed.append(event.seq)
    return doomed


def _feed_floor(
    acks: FeedAckFloor | None,
    requested: int,
    *,
    past_feed_acks: bool,
    released: frozenset[str],
) -> tuple[int, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Return the floor to apply, the consumers that hold it and the consumers passed."""
    if acks is None:
        return requested, (), ()
    if past_feed_acks:
        return requested, (), acks.holding(requested)
    kept = tuple(item for item in acks.consumers if item[0] not in released)
    floor = min([requested, *(acked for _, acked in kept)])
    held = tuple(item for item in kept if item[1] < requested)
    passed = tuple(item for item in acks.consumers if item[0] in released and item[1] < floor)
    return floor, held, passed


def _resolve_feed_floor(
    store: EventStore,
    requested: int,
    *,
    past_feed_acks: bool,
    released: frozenset[str],
) -> tuple[int, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Read the registered consumers, refuse an unknown release, and return the feed floor."""
    acks = feed_ack_floor(store)
    registered = {name for name, _ in acks.consumers} if acks is not None else set()
    unknown = sorted(released - registered)
    if unknown:
        raise FeedConsumerUnknown(f"not a registered feed consumer: {', '.join(unknown)}")
    return _feed_floor(acks, requested, past_feed_acks=past_feed_acks, released=released)


def compact(
    store: EventStore,
    policy: RetentionPolicy,
    *,
    floor_seq: int,
    now: float | None = None,
    dry_run: bool = False,
    past_feed_acks: bool = False,
    release_feed_consumers: Collection[str] = (),
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
        it is touched. To preserve unread events, choose a floor at or below every
        consumer's durably ingested cursor. The function does not verify consumer
        progress. Use :meth:`~synapse_channel.core.persistence.EventStore.max_seq`
        only when every consumer has durably ingested the whole log.
    now : float or None, optional
        Wall-clock time used to age out findings; the system clock is used when
        ``None``.
    dry_run : bool, optional
        Calculate and return the exact deletion counts without changing the
        store. Used to persist a recovery archive before destructive removal.
    past_feed_acks : bool, optional
        Apply ``floor_seq`` even above a registered feed consumer's
        acknowledgement. The consumers passed are journalled with the floor.
    release_feed_consumers : collection of str, optional
        Registered feed consumers to leave out of the floor, such as one that is
        gone for good; the others still hold it. A released consumer whose
        acknowledgement lies below the floor applied is journalled as passed.
        The release is journalled with the floor and holds in every later run
        until that consumer acknowledges again.

    Returns
    -------
    CompactionResult
        Counts of what was removed and the floor that was honoured.

    Raises
    ------
    ValueError
        When ``past_feed_acks`` and ``release_feed_consumers`` are both given.
    FeedConsumerUnknown
        When a released name holds no acknowledgement in the journal's current
        instance; nothing is deleted.

    Notes
    -----
    A sweep that removes a row, or passes a registered feed consumer, journals
    the floor it applied as a ``compaction_floor`` event, so the feed can tell a
    consumer whose cursor lies below it that rows there may be gone. A dry run,
    and a sweep that removes nothing and passes no one, journals nothing.
    """
    requested = int(floor_seq)
    released = frozenset(release_feed_consumers)
    if past_feed_acks and released:
        raise ValueError("pass past_feed_acks or release_feed_consumers, not both")
    if policy.is_noop:
        return CompactionResult(checkpoints_removed=0, findings_removed=0, floor_seq=requested)
    floor, held, passed = _resolve_feed_floor(
        store, requested, past_feed_acks=past_feed_acks, released=released
    )
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
    doomed_corrupt = (
        [row.seq for row in store.corrupt_rows(through_seq=floor)]
        if policy.drop_corrupt_rows
        else []
    )

    seqs = doomed_checkpoints + doomed_findings + doomed_corrupt
    if not dry_run and (seqs or passed):
        instance = store.journal_instance()
        record_compaction_floor(
            store,
            journal_instance=instance.instance if instance is not None else "",
            requested_floor=requested,
            applied_floor=floor,
            past_feed_acks=past_feed_acks,
            held_by=held,
            passed_consumers=passed,
            removed=len(seqs),
            released=tuple(sorted(released)),
        )
        if seqs:
            store.delete(seqs)
    return CompactionResult(
        checkpoints_removed=len(doomed_checkpoints),
        findings_removed=len(doomed_findings),
        floor_seq=floor,
        corrupt_rows_removed=len(doomed_corrupt),
        requested_floor_seq=None if floor == requested else requested,
        held_by_feed_acks=held,
        passed_feed_acks=passed,
    )
