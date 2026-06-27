# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replayable postmortem reports from durable events
"""Build read-only task postmortems from the durable hub event log.

The report is forensic evidence, not a policy verdict. It reconstructs what the
event log can prove: task snapshots, releases, progress evidence, path-overlap
conflicts involving the task, and candidate unanswered directed messages that
mention the task id. It does not contact a live hub and it does not infer intent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent

SNAPSHOT_KINDS = frozenset(
    {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}
)
"""Event kinds whose payload is a task-claim snapshot."""

TIMELINE_KINDS = SNAPSHOT_KINDS | {EventKind.RELEASE, EventKind.LEDGER_PROGRESS}
"""Task-scoped event kinds rendered in the postmortem timeline."""


@dataclass(frozen=True)
class TimelineEntry:
    """One event in the task postmortem timeline.

    Attributes
    ----------
    seq : int
        Durable event-log sequence.
    ts : float
        Event timestamp.
    kind : str
        Durable event kind.
    actor : str
        Best-effort actor field for the event: owner, author, sender, or empty.
    status : str
        Task/claim status when the event carries one.
    text : str
        Human-readable payload summary.
    payload : dict[str, Any]
        Original JSON payload persisted for the event.
    """

    seq: int
    ts: float
    kind: str
    actor: str
    status: str
    text: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class EvidenceNote:
    """One assessment/progress note that existed for the task."""

    seq: int
    ts: float
    author: str
    kind: str
    text: str


@dataclass(frozen=True)
class UnansweredMessage:
    """Candidate directed chat that mentioned the task and had no later chat reply.

    This is intentionally labelled as a candidate signal. The event log proves
    the directed message and the absence of a later matching chat response; it
    cannot prove the recipient's intent or whether they responded elsewhere.
    """

    seq: int
    ts: float
    sender: str
    target: str
    payload: str


@dataclass(frozen=True)
class TaskPostmortem:
    """Replayable postmortem for one task id.

    Attributes
    ----------
    task_id : str
        Task identifier being reconstructed.
    generated_from_seq : int
        Highest event sequence considered.
    timeline : tuple[TimelineEntry, ...]
        Task-relevant durable events in sequence order.
    owners : tuple[str, ...]
        Owners observed in task snapshots.
    releases : tuple[TimelineEntry, ...]
        Release events for the task.
    evidence_notes : tuple[EvidenceNote, ...]
        Assessment/progress notes for the task.
    conflicts : tuple[dict[str, object], ...]
        Reconstructed live path-overlap conflicts involving the task.
    unanswered_messages : tuple[UnansweredMessage, ...]
        Candidate directed messages mentioning the task with no later chat reply.
    """

    task_id: str
    generated_from_seq: int
    timeline: tuple[TimelineEntry, ...]
    owners: tuple[str, ...]
    releases: tuple[TimelineEntry, ...]
    evidence_notes: tuple[EvidenceNote, ...]
    conflicts: tuple[dict[str, object], ...]
    unanswered_messages: tuple[UnansweredMessage, ...]


def run_task_postmortem(db_path: str | Path, task_id: str) -> TaskPostmortem:
    """Build a task postmortem from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    task_id : str
        Task id to reconstruct.

    Returns
    -------
    TaskPostmortem
        Replayable report built from persisted events.

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
    return build_task_postmortem(task_id, events)


def build_task_postmortem(task_id: str, events: Sequence[StoredEvent]) -> TaskPostmortem:
    """Build a postmortem for ``task_id`` from loaded events."""
    clean_task_id = task_id.strip()
    owners = _owners_for_task(clean_task_id, events)
    timeline = tuple(
        _timeline_entry(event)
        for event in events
        if _event_belongs_to_task(event, clean_task_id, owners)
    )
    evidence = tuple(
        _evidence_note(event)
        for event in events
        if event.kind == EventKind.LEDGER_PROGRESS
        and _event_task_id(event) == clean_task_id
        and _is_evidence_note(event)
    )
    return TaskPostmortem(
        task_id=clean_task_id,
        generated_from_seq=max((event.seq for event in events), default=0),
        timeline=timeline,
        owners=owners,
        releases=tuple(entry for entry in timeline if entry.kind == EventKind.RELEASE),
        evidence_notes=evidence,
        conflicts=_conflicts_for_task(clean_task_id, events),
        unanswered_messages=_unanswered_messages(clean_task_id, events, owners),
    )


def postmortem_to_json(report: TaskPostmortem) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a postmortem."""
    return {
        "task_id": report.task_id,
        "generated_from_seq": report.generated_from_seq,
        "owners": list(report.owners),
        "timeline": [_timeline_to_json(entry) for entry in report.timeline],
        "releases": [_timeline_to_json(entry) for entry in report.releases],
        "evidence_notes": [_evidence_to_json(note) for note in report.evidence_notes],
        "conflicts": [dict(item) for item in report.conflicts],
        "unanswered_messages": [_message_to_json(item) for item in report.unanswered_messages],
    }


def render_markdown(report: TaskPostmortem) -> str:
    """Render a postmortem as compact Markdown for handovers or incidents."""
    if not report.timeline:
        return f"# Postmortem: {report.task_id}\n\nNo task events found."

    lines = [
        f"# Postmortem: {report.task_id}",
        "",
        f"- Generated from event seq: {report.generated_from_seq}",
        f"- Owners: {', '.join(report.owners) if report.owners else '-'}",
        f"- Releases: {len(report.releases)}",
        f"- Conflicts: {len(report.conflicts)}",
        f"- Candidate unanswered messages: {len(report.unanswered_messages)}",
        "",
        "## Timeline",
    ]
    lines.extend(_render_timeline_entry(entry) for entry in report.timeline)
    lines.append("")
    lines.append("## Conflicts")
    if report.conflicts:
        lines.extend(_render_conflict(item) for item in report.conflicts)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Evidence")
    if report.evidence_notes:
        lines.extend(_render_evidence(note) for note in report.evidence_notes)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Candidate Unanswered Messages")
    if report.unanswered_messages:
        lines.extend(_render_message(message) for message in report.unanswered_messages)
    else:
        lines.append("- none")
    return "\n".join(lines)


def _event_belongs_to_task(
    event: StoredEvent,
    task_id: str,
    owners: Sequence[str],
) -> bool:
    """Return whether an event belongs in the task timeline."""
    if event.kind in TIMELINE_KINDS and _event_task_id(event) == task_id:
        return True
    if event.kind == EventKind.CHAT and _mentions_task(event, task_id):
        target = _chat_target(event)
        return target in owners or _chat_sender(event) in owners
    return False


def _owners_for_task(task_id: str, events: Sequence[StoredEvent]) -> tuple[str, ...]:
    """Return unique owners observed in task snapshots."""
    return _unique_ordered(
        str(event.payload.get("owner", ""))
        for event in events
        if event.kind in SNAPSHOT_KINDS and _event_task_id(event) == task_id
    )


def _timeline_entry(event: StoredEvent) -> TimelineEntry:
    """Project a stored event into the postmortem timeline shape."""
    payload = event.payload
    return TimelineEntry(
        seq=event.seq,
        ts=event.ts,
        kind=event.kind,
        actor=_event_actor(event),
        status=str(payload.get("status", "")),
        text=_event_text(event),
        payload=payload,
    )


def _event_actor(event: StoredEvent) -> str:
    """Return the best actor field carried by an event."""
    payload = event.payload
    for key in ("owner", "author", "from", "sender"):
        value = str(payload.get(key, ""))
        if value:
            return value
    return ""


def _event_text(event: StoredEvent) -> str:
    """Return the most useful text field carried by an event."""
    payload = event.payload
    for key in ("text", "payload", "note", "data_ref"):
        value = str(payload.get(key, ""))
        if value:
            return value
    return ""


def _event_task_id(event: StoredEvent) -> str:
    """Return an event payload's task id."""
    return str(event.payload.get("task_id", ""))


def _evidence_note(event: StoredEvent) -> EvidenceNote:
    """Project a progress event into an evidence note."""
    payload = event.payload
    return EvidenceNote(
        seq=event.seq,
        ts=event.ts,
        author=str(payload.get("author", "")),
        kind=str(payload.get("kind", "")),
        text=str(payload.get("text", "")),
    )


def _is_evidence_note(event: StoredEvent) -> bool:
    """Return whether a progress event should count as postmortem evidence."""
    kind = str(event.payload.get("kind", ""))
    text = str(event.payload.get("text", ""))
    return kind == "assessment" or text.startswith("release receipt:")


def _conflicts_for_task(
    task_id: str,
    events: Sequence[StoredEvent],
) -> tuple[dict[str, object], ...]:
    """Reconstruct path-overlap conflicts involving ``task_id`` over time."""
    live: dict[str, StoredEvent] = {}
    conflicts: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for event in events:
        current_task = _event_task_id(event)
        if event.kind == EventKind.RELEASE and current_task:
            live.pop(current_task, None)
            continue
        if event.kind not in SNAPSHOT_KINDS or not current_task:
            continue
        live[current_task] = event
        target = live.get(task_id)
        if target is None:
            continue
        for other_task, other in sorted(live.items()):
            if other_task == task_id:
                continue
            conflict = _conflict_at_event(event, task_id, target, other_task, other)
            if conflict is None:
                continue
            raw_paths = conflict.get("paths", ())
            paths = tuple(str(path) for path in raw_paths) if isinstance(raw_paths, list) else ()
            key = (
                str(conflict["left_task"]),
                str(conflict["right_task"]),
                str(conflict["right_owner"]),
                paths,
            )
            if key not in seen:
                seen.add(key)
                conflicts.append(conflict)
    return tuple(conflicts)


def _conflict_at_event(
    event: StoredEvent,
    task_id: str,
    target: StoredEvent,
    other_task: str,
    other: StoredEvent,
) -> dict[str, object] | None:
    """Return one conflict record when target and other overlap."""
    target_owner = str(target.payload.get("owner", ""))
    other_owner = str(other.payload.get("owner", ""))
    target_worktree = str(target.payload.get("worktree", ""))
    other_worktree = str(other.payload.get("worktree", ""))
    target_paths = _paths_from_event(target)
    other_paths = _paths_from_event(other)
    if target_owner == other_owner or target_worktree != other_worktree:
        return None
    if not _paths_overlap_many(target_paths, other_paths):
        return None
    return {
        "seq": event.seq,
        "ts": event.ts,
        "left_task": task_id,
        "left_owner": target_owner,
        "right_task": other_task,
        "right_owner": other_owner,
        "worktree": target_worktree,
        "paths": list(_unique_ordered((*target_paths, *other_paths))),
    }


def _unanswered_messages(
    task_id: str,
    events: Sequence[StoredEvent],
    owners: Sequence[str],
) -> tuple[UnansweredMessage, ...]:
    """Return candidate directed messages about the task without later chat replies."""
    candidates: list[UnansweredMessage] = []
    owner_set = set(owners)
    for event in events:
        if event.kind != EventKind.CHAT or not _mentions_task(event, task_id):
            continue
        target = _chat_target(event)
        if target not in owner_set or target in {"", "all"}:
            continue
        if not _has_later_chat_reply(events, event.seq, sender=target, task_id=task_id):
            candidates.append(
                UnansweredMessage(
                    seq=event.seq,
                    ts=event.ts,
                    sender=_chat_sender(event),
                    target=target,
                    payload=str(event.payload.get("payload", "")),
                )
            )
    return tuple(candidates)


def _has_later_chat_reply(
    events: Sequence[StoredEvent],
    after_seq: int,
    *,
    sender: str,
    task_id: str,
) -> bool:
    """Return whether ``sender`` later sent a chat mentioning ``task_id``."""
    return any(
        event.seq > after_seq
        and event.kind == EventKind.CHAT
        and _chat_sender(event) == sender
        and _mentions_task(event, task_id)
        for event in events
    )


def _mentions_task(event: StoredEvent, task_id: str) -> bool:
    """Return whether a chat payload mentions the task id literally."""
    return task_id in str(event.payload.get("payload", ""))


def _chat_sender(event: StoredEvent) -> str:
    """Return the sender field from a chat event payload."""
    return str(event.payload.get("from", event.payload.get("sender", "")))


def _chat_target(event: StoredEvent) -> str:
    """Return the target field from a chat event payload."""
    return str(event.payload.get("target", ""))


def _paths_from_event(event: StoredEvent) -> tuple[str, ...]:
    """Return a task snapshot's path scopes."""
    return tuple(str(path) for path in event.payload.get("paths", ()))


def _paths_overlap_many(left: Sequence[str], right: Sequence[str]) -> bool:
    """Return whether two path-scope sets overlap."""
    if not left or not right:
        return True
    return any(
        _path_pair_overlaps(left_path, right_path) for left_path in left for right_path in right
    )


def _path_pair_overlaps(left: str, right: str) -> bool:
    """Return whether two repository-relative paths overlap."""
    left_clean = left.rstrip("/")
    right_clean = right.rstrip("/")
    return (
        left_clean == right_clean
        or left_clean.startswith(f"{right_clean}/")
        or right_clean.startswith(f"{left_clean}/")
    )


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    """Return non-empty values without duplicates while preserving order."""
    return tuple(dict.fromkeys(value for value in values if value))


def _timeline_to_json(entry: TimelineEntry) -> dict[str, object]:
    """Convert a timeline entry into JSON-compatible fields."""
    return {
        "seq": entry.seq,
        "ts": entry.ts,
        "kind": entry.kind,
        "actor": entry.actor,
        "status": entry.status,
        "text": entry.text,
        "payload": entry.payload,
    }


def _evidence_to_json(note: EvidenceNote) -> dict[str, object]:
    """Convert an evidence note into JSON-compatible fields."""
    return {
        "seq": note.seq,
        "ts": note.ts,
        "author": note.author,
        "kind": note.kind,
        "text": note.text,
    }


def _message_to_json(message: UnansweredMessage) -> dict[str, object]:
    """Convert a candidate unanswered message into JSON-compatible fields."""
    return {
        "seq": message.seq,
        "ts": message.ts,
        "sender": message.sender,
        "target": message.target,
        "payload": message.payload,
    }


def _render_timeline_entry(entry: TimelineEntry) -> str:
    """Render one timeline entry."""
    actor = f" actor={entry.actor}" if entry.actor else ""
    status = f" status={entry.status}" if entry.status else ""
    text = f" — {entry.text}" if entry.text else ""
    return f"- seq={entry.seq} ts={entry.ts:.3f} kind={entry.kind}{actor}{status}{text}"


def _render_conflict(item: dict[str, object]) -> str:
    """Render one conflict record."""
    raw_paths = item.get("paths", ())
    paths = ", ".join(str(path) for path in raw_paths) if isinstance(raw_paths, list) else ""
    return (
        f"- seq={item['seq']} {item['left_task']}@{item['left_owner']} overlaps "
        f"{item['right_task']}@{item['right_owner']} in {item['worktree']} paths={paths}"
    )


def _render_evidence(note: EvidenceNote) -> str:
    """Render one evidence note."""
    return f"- seq={note.seq} author={note.author} kind={note.kind} — {note.text}"


def _render_message(message: UnansweredMessage) -> str:
    """Render one candidate unanswered message."""
    return f"- seq={message.seq} from={message.sender} to={message.target} — {message.payload}"
