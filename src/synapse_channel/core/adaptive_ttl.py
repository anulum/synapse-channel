# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only adaptive lease TTL advice
"""Build advisory lease-TTL recommendations from durable coordination events.

The evaluator never changes hub defaults and never overrides explicit
``ttl_seconds`` supplied by an operator or agent. It reconstructs completed task
duration samples from claim snapshots and release events, reports current live
claim load, and returns bounded recommendations that a human can apply manually.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent

SNAPSHOT_KINDS = frozenset(
    {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}
)
"""Event kinds whose payload is a task-claim snapshot."""

DEFAULT_CURRENT_TTL_SECONDS = 3600.0
"""Fallback TTL used when no explicit current default is supplied."""

DEFAULT_MIN_TTL_SECONDS = 30.0
"""Lower bound for generated TTL recommendations, in seconds."""

DEFAULT_MAX_TTL_SECONDS = 14_400.0
"""Upper bound for generated TTL recommendations, in seconds."""

DEFAULT_SAFETY_MULTIPLIER = 1.5
"""Multiplier applied to observed p90 durations before clamping."""

DEFAULT_MIN_SAMPLES = 3
"""Minimum completed-task samples required before changing the recommendation."""

DEFAULT_MIN_OWNER_SAMPLES = 3
"""Minimum per-owner samples required before emitting owner-specific advice."""


@dataclass(frozen=True)
class CompletedLeaseSample:
    """One completed task duration reconstructed from the event log.

    Attributes
    ----------
    task_id : str
        Released task id.
    owner : str
        Owner of the latest task snapshot before release.
    duration_seconds : float
        Seconds between the first live snapshot in the lease segment and release.
    """

    task_id: str
    owner: str
    duration_seconds: float


@dataclass(frozen=True)
class LeaseTtlOwnerAdvice:
    """Owner-specific TTL advice derived from completed samples."""

    owner: str
    sample_count: int
    p90_seconds: float
    recommended_seconds: float


@dataclass(frozen=True)
class LeaseTtlAdvice:
    """Read-only adaptive lease TTL advice."""

    generated_from_seq: int
    as_of: float
    sample_count: int
    active_claims: int
    stale_claims: int
    current_default_seconds: float
    p90_seconds: float
    recommended_default_seconds: float
    confidence: str
    owner_advice: tuple[LeaseTtlOwnerAdvice, ...]
    notes: tuple[str, ...]


def run_ttl_advice(
    db_path: str | Path,
    *,
    as_of: float | None = None,
    current_default_seconds: float = DEFAULT_CURRENT_TTL_SECONDS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_owner_samples: int = DEFAULT_MIN_OWNER_SAMPLES,
    min_ttl_seconds: float = DEFAULT_MIN_TTL_SECONDS,
    max_ttl_seconds: float = DEFAULT_MAX_TTL_SECONDS,
    safety_multiplier: float = DEFAULT_SAFETY_MULTIPLIER,
) -> LeaseTtlAdvice:
    """Build lease TTL advice from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    as_of : float or None, optional
        Timestamp used to count stale live claims. The latest event timestamp is
        used when omitted.
    current_default_seconds : float, optional
        Current operator default TTL. Low-sample advice preserves this value.
    min_samples : int, optional
        Minimum completed samples needed before recommending a new default.
    min_owner_samples : int, optional
        Minimum completed samples needed for owner-specific advice.
    min_ttl_seconds : float, optional
        Lower clamp for advice.
    max_ttl_seconds : float, optional
        Upper clamp for advice.
    safety_multiplier : float, optional
        Multiplier applied to observed p90 durations.

    Returns
    -------
    LeaseTtlAdvice
        Advisory lease TTL report.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return build_ttl_advice(
        events,
        as_of=as_of,
        current_default_seconds=current_default_seconds,
        min_samples=min_samples,
        min_owner_samples=min_owner_samples,
        min_ttl_seconds=min_ttl_seconds,
        max_ttl_seconds=max_ttl_seconds,
        safety_multiplier=safety_multiplier,
    )


def build_ttl_advice(
    events: Sequence[StoredEvent],
    *,
    as_of: float | None = None,
    current_default_seconds: float = DEFAULT_CURRENT_TTL_SECONDS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_owner_samples: int = DEFAULT_MIN_OWNER_SAMPLES,
    min_ttl_seconds: float = DEFAULT_MIN_TTL_SECONDS,
    max_ttl_seconds: float = DEFAULT_MAX_TTL_SECONDS,
    safety_multiplier: float = DEFAULT_SAFETY_MULTIPLIER,
) -> LeaseTtlAdvice:
    """Build read-only adaptive TTL advice from loaded events."""
    clean_min = max(1, int(min_samples))
    clean_owner_min = max(1, int(min_owner_samples))
    floor = _positive_float(min_ttl_seconds, DEFAULT_MIN_TTL_SECONDS)
    ceiling = max(floor, _positive_float(max_ttl_seconds, DEFAULT_MAX_TTL_SECONDS))
    current = _clamp(
        _positive_float(current_default_seconds, DEFAULT_CURRENT_TTL_SECONDS),
        floor,
        ceiling,
    )
    multiplier = _positive_float(safety_multiplier, DEFAULT_SAFETY_MULTIPLIER)
    cutoff = _effective_as_of(events, as_of)
    samples, live = _samples_and_live_claims(events)
    durations = tuple(sample.duration_seconds for sample in samples)
    p90 = _nearest_rank_percentile(durations, 0.9)
    recommended = current
    if len(samples) >= clean_min:
        recommended = _clamp(p90 * multiplier, floor, ceiling)
    stale_claims = sum(1 for event in live.values() if _lease_expires_at(event) <= cutoff)
    return LeaseTtlAdvice(
        generated_from_seq=max((event.seq for event in events), default=0),
        as_of=cutoff,
        sample_count=len(samples),
        active_claims=len(live),
        stale_claims=stale_claims,
        current_default_seconds=current,
        p90_seconds=p90,
        recommended_default_seconds=recommended,
        confidence=_confidence(len(samples), clean_min),
        owner_advice=_owner_advice(samples, clean_owner_min, floor, ceiling, multiplier),
        notes=_notes(len(samples), clean_min, stale_claims),
    )


def ttl_advice_to_json(report: LeaseTtlAdvice) -> dict[str, object]:
    """Return a stable JSON-compatible representation of TTL advice."""
    return {
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "sample_count": report.sample_count,
        "active_claims": report.active_claims,
        "stale_claims": report.stale_claims,
        "current_default_seconds": report.current_default_seconds,
        "p90_seconds": report.p90_seconds,
        "recommended_default_seconds": report.recommended_default_seconds,
        "confidence": report.confidence,
        "owner_advice": [_owner_to_json(item) for item in report.owner_advice],
        "notes": list(report.notes),
    }


def render_human(report: LeaseTtlAdvice) -> str:
    """Render TTL advice as compact terminal text."""
    lines = [
        "Lease TTL advice: advisory, manual TTL preserved",
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        (
            f"samples={report.sample_count} p90_seconds={report.p90_seconds:.3f} "
            f"recommended_default_seconds={report.recommended_default_seconds:.3f} "
            f"confidence={report.confidence}"
        ),
        f"active_claims={report.active_claims} stale_claims={report.stale_claims}",
        f"notes={','.join(report.notes)}",
    ]
    if report.owner_advice:
        lines.append("Owner advice")
        lines.extend(_render_owner(item) for item in report.owner_advice)
    return "\n".join(lines)


def _samples_and_live_claims(
    events: Sequence[StoredEvent],
) -> tuple[tuple[CompletedLeaseSample, ...], dict[str, StoredEvent]]:
    """Return completed duration samples and live unreleased snapshots."""
    starts: dict[str, float] = {}
    live: dict[str, StoredEvent] = {}
    samples: list[CompletedLeaseSample] = []
    for event in events:
        task_id = _task_id(event)
        if not task_id:
            continue
        if event.kind in SNAPSHOT_KINDS:
            starts.setdefault(task_id, _claimed_at(event, event.ts))
            live[task_id] = event
            continue
        if event.kind == EventKind.RELEASE and task_id in live:
            start = starts.pop(task_id, event.ts)
            latest = live.pop(task_id)
            samples.append(
                CompletedLeaseSample(
                    task_id=task_id,
                    owner=_owner(latest),
                    duration_seconds=max(0.0, event.ts - start),
                )
            )
    return tuple(samples), live


def _owner_advice(
    samples: Sequence[CompletedLeaseSample],
    min_owner_samples: int,
    floor: float,
    ceiling: float,
    multiplier: float,
) -> tuple[LeaseTtlOwnerAdvice, ...]:
    """Return per-owner advice for owners with enough completed samples."""
    by_owner: defaultdict[str, list[float]] = defaultdict(list)
    for sample in samples:
        by_owner[sample.owner].append(sample.duration_seconds)
    advice: list[LeaseTtlOwnerAdvice] = []
    for owner, durations in sorted(by_owner.items()):
        if len(durations) < min_owner_samples:
            continue
        p90 = _nearest_rank_percentile(tuple(durations), 0.9)
        advice.append(
            LeaseTtlOwnerAdvice(
                owner=owner,
                sample_count=len(durations),
                p90_seconds=p90,
                recommended_seconds=_clamp(p90 * multiplier, floor, ceiling),
            )
        )
    return tuple(advice)


def _effective_as_of(events: Sequence[StoredEvent], explicit: float | None) -> float:
    """Return the cutoff timestamp for stale live-claim detection."""
    if explicit is not None:
        return float(explicit)
    return max((event.ts for event in events), default=0.0)


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank percentile for a non-negative sample set."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(float(percentile) * len(ordered)))
    return ordered[rank - 1]


def _confidence(sample_count: int, min_samples: int) -> str:
    """Return a conservative confidence label based only on sample count."""
    if sample_count == 0:
        return "none"
    if sample_count < min_samples:
        return "low"
    if sample_count < 10:
        return "medium"
    return "high"


def _notes(sample_count: int, min_samples: int, stale_claims: int) -> tuple[str, ...]:
    """Return stable report caveats."""
    notes = ["manual_ttl_preserved", "read_only_event_log"]
    if sample_count < min_samples:
        notes.append("insufficient_samples_fallback")
    if stale_claims:
        notes.append("stale_claims_present")
    return tuple(notes)


def _task_id(event: StoredEvent) -> str:
    """Return a task id carried by an event payload."""
    return str(event.payload.get("task_id", ""))


def _owner(event: StoredEvent) -> str:
    """Return the owner carried by a task snapshot."""
    return str(event.payload.get("owner", ""))


def _claimed_at(event: StoredEvent, fallback: float) -> float:
    """Return the claim start timestamp carried by a task snapshot."""
    return float(event.payload.get("claimed_at", fallback))


def _lease_expires_at(event: StoredEvent) -> float:
    """Return the lease expiry timestamp carried by a task snapshot."""
    return float(event.payload.get("lease_expires_at", 0.0))


def _positive_float(value: float, fallback: float) -> float:
    """Return ``value`` when it is positive and finite, otherwise ``fallback``."""
    clean = float(value)
    if clean > 0.0 and math.isfinite(clean):
        return clean
    return fallback


def _clamp(value: float, floor: float, ceiling: float) -> float:
    """Clamp ``value`` to the inclusive ``floor``/``ceiling`` range."""
    return min(max(value, floor), ceiling)


def _owner_to_json(item: LeaseTtlOwnerAdvice) -> dict[str, object]:
    """Convert owner advice into JSON-compatible fields."""
    return {
        "owner": item.owner,
        "sample_count": item.sample_count,
        "p90_seconds": item.p90_seconds,
        "recommended_seconds": item.recommended_seconds,
    }


def _render_owner(item: LeaseTtlOwnerAdvice) -> str:
    """Render one owner-specific advice row."""
    return (
        f"- {item.owner}: samples={item.sample_count} p90_seconds={item.p90_seconds:.3f} "
        f"recommended_seconds={item.recommended_seconds:.3f}"
    )
