# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verify a task replays deterministically to a state digest
"""Verify a task reproduces deterministically from the durable event log.

The hub's state is a pure fold of an append-only log, so a task's authoritative
history must replay to the *same* state every time and on every machine. This
module makes that property checkable: it canonicalises the task's authoritative
event slice (claim snapshots and releases, in sequence order) and hashes it into
a stable SHA-256 digest.

Two operators — or two federated hubs — holding the same slice derive the same
digest, so the digest is a portable fingerprint of "what happened to this task".
``--expect`` turns it into a gate: a mismatch proves the log slice differs, the
same way :mod:`synapse_channel.cli_verify_release` gates a release receipt. It is
read-only and contacts no live hub.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS, reconstruct_claim

AUTHORITATIVE_KINDS = SNAPSHOT_KINDS | {EventKind.RELEASE}
"""Event kinds that fold into a task's authoritative state and so define its digest."""


@dataclass(frozen=True)
class ReproductionReport:
    """A deterministic-replay fingerprint for one task.

    Attributes
    ----------
    task_id : str
        Task the digest covers.
    present : bool
        Whether any authoritative events exist for the task. When ``False`` the
        digest is the canonical digest of an empty slice.
    digest : str
        SHA-256 hex digest of the canonical authoritative event slice.
    event_count : int
        Number of authoritative events folded into the digest.
    first_seq : int
        Sequence of the first authoritative event, or ``0`` when none.
    last_seq : int
        Sequence of the last authoritative event, or ``0`` when none.
    final_owner : str
        Owner of the task's final reconstructed claim, or empty when released or
        never claimed.
    final_status : str
        Final state marker: the claim status, ``"released"`` when the task ended
        released, or ``"absent"`` when no authoritative events exist.
    """

    task_id: str
    present: bool
    digest: str
    event_count: int
    first_seq: int
    last_seq: int
    final_owner: str
    final_status: str


def authoritative_slice(
    task_id: str,
    events: Sequence[StoredEvent],
) -> tuple[StoredEvent, ...]:
    """Return a task's authoritative events in sequence order.

    Parameters
    ----------
    task_id : str
        Task to slice.
    events : Sequence[StoredEvent]
        Events to filter, in any order.

    Returns
    -------
    tuple[StoredEvent, ...]
        The task's claim snapshots and releases, ordered by ascending sequence.
    """
    clean = task_id.strip()
    selected = [
        event
        for event in events
        if event.kind in AUTHORITATIVE_KINDS and _event_task_id(event) == clean
    ]
    return tuple(sorted(selected, key=lambda item: item.seq))


def canonical_bytes(slice_events: Sequence[StoredEvent]) -> bytes:
    """Return the stable canonical encoding of an event slice.

    The encoding is sequence-ordered and key-sorted so the same slice always
    serialises to the same bytes regardless of insertion or read order.

    Parameters
    ----------
    slice_events : Sequence[StoredEvent]
        Events to encode (assumed already in sequence order).

    Returns
    -------
    bytes
        UTF-8 canonical JSON of ``[{"seq", "kind", "payload"}, ...]``.
    """
    canonical = [
        {"seq": event.seq, "kind": event.kind, "payload": event.payload} for event in slice_events
    ]
    return json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def slice_digest(slice_events: Sequence[StoredEvent]) -> str:
    """Return the SHA-256 hex digest of an event slice's canonical encoding."""
    return hashlib.sha256(canonical_bytes(slice_events)).hexdigest()


def reproduce_task(task_id: str, events: Sequence[StoredEvent]) -> ReproductionReport:
    """Build a deterministic-replay report for ``task_id``.

    Parameters
    ----------
    task_id : str
        Task to fingerprint.
    events : Sequence[StoredEvent]
        Loaded events.

    Returns
    -------
    ReproductionReport
        The task's authoritative-slice digest and final reconstructed state.
    """
    clean = task_id.strip()
    slice_events = authoritative_slice(clean, events)
    digest = slice_digest(slice_events)
    final = reconstruct_claim(clean, events)
    if not slice_events:
        final_status = "absent"
    elif final is None:
        final_status = "released"
    else:
        final_status = final.status
    return ReproductionReport(
        task_id=clean,
        present=bool(slice_events),
        digest=digest,
        event_count=len(slice_events),
        first_seq=slice_events[0].seq if slice_events else 0,
        last_seq=slice_events[-1].seq if slice_events else 0,
        final_owner=final.owner if final is not None else "",
        final_status=final_status,
    )


def verify_reproduction(report: ReproductionReport, expected_digest: str) -> bool:
    """Return whether a report's digest matches an expected digest.

    Uses a constant-time comparison so the check does not leak digest bytes
    through timing, mirroring release-receipt verification.

    Parameters
    ----------
    report : ReproductionReport
        The freshly computed report.
    expected_digest : str
        The digest to verify against; surrounding whitespace and case are
        ignored.

    Returns
    -------
    bool
        ``True`` when the digests match exactly.
    """
    return hmac.compare_digest(report.digest, expected_digest.strip().lower())


def run_reproduction(
    db_path: str | Path,
    task_id: str,
    *,
    key_file: str | Path | None = None,
) -> ReproductionReport:
    """Build a reproduction report from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    task_id : str
        Task id to fingerprint.

    Returns
    -------
    ReproductionReport
        The report built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return reproduce_task(task_id, events)


def reproduction_to_json(report: ReproductionReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a reproduction report."""
    return {
        "task_id": report.task_id,
        "present": report.present,
        "digest": report.digest,
        "event_count": report.event_count,
        "first_seq": report.first_seq,
        "last_seq": report.last_seq,
        "final_owner": report.final_owner,
        "final_status": report.final_status,
    }


def render_markdown(report: ReproductionReport) -> str:
    """Render a reproduction report as compact Markdown."""
    if not report.present:
        return f"# Reproduce: {report.task_id}\n\nNo authoritative events found."
    return "\n".join(
        [
            f"# Reproduce: {report.task_id}",
            "",
            f"- Digest (sha256): {report.digest}",
            f"- Authoritative events: {report.event_count}",
            f"- Sequence range: {report.first_seq}..{report.last_seq}",
            f"- Final owner: {report.final_owner or '-'}",
            f"- Final status: {report.final_status}",
        ]
    )


def _event_task_id(event: StoredEvent) -> str:
    """Return an event payload's task id."""
    return str(event.payload.get("task_id", ""))
