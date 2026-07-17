# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator HTML archive reports for compacted event logs
"""Render compacted coordination history into an operator-readable HTML report.

The durable event store is the audit spine for claims, task board progress, and
release receipts. Compaction deliberately removes settled checkpoint/finding
events, so an operator may need a static report that records what was present
before the retention sweep and what the sweep removed. This module produces that
report without adding a templating dependency or exposing raw unescaped event
payloads to the browser.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from synapse_channel.core.compaction import CompactionResult
from synapse_channel.core.event_row_recovery import CorruptEventRow
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

REPORT_FILE_MODE = 0o600
"""Filesystem mode used for archive reports because they may contain local evidence."""

TIMELINE_EXCLUDED_KINDS = frozenset(
    {
        EventKind.CHAT,
        EventKind.RECALL,
        EventKind.FINDING,
        EventKind.IDEMPOTENCY,
    }
)
"""Event kinds hidden from the human timeline section."""

RELEASE_RECEIPT_PREFIX = "release receipt:"
"""Prefix used by receipt-bearing board assessment notes."""


@dataclass(frozen=True)
class ArchiveReportOptions:
    """Rendering options for one archive report.

    Attributes
    ----------
    source_path : str
        Event-store path the report summarises.
    generated_at : float
        Wall-clock timestamp, in seconds, recorded in the report header.
    max_items : int, optional
        Maximum number of rows shown in bounded sections. Values below ``1`` are
        clamped to ``1`` so an operator never receives an apparently empty report
        from a non-empty store.
    compaction_completed : bool, optional
        Whether removal completed. ``False`` labels a pre-delete recovery archive
        as planned so an interrupted command never overstates deletion.
    """

    source_path: str
    generated_at: float
    max_items: int = 200
    compaction_completed: bool = True

    @property
    def bounded_max_items(self) -> int:
        """Return ``max_items`` clamped to the valid display range."""
        return max(int(self.max_items), 1)


def _stamp(ts: float) -> str:
    """Return ``ts`` as a compact UTC ISO-8601 timestamp."""
    return (
        datetime.fromtimestamp(float(ts), tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _text(value: object) -> str:
    """Return a stripped string value for display."""
    return str(value).strip()


def _event_task(payload: dict[str, Any]) -> str:
    """Return an event's task id field when present."""
    return _text(payload.get("task_id", ""))


def _claim_summary(payload: dict[str, Any], action: str) -> str:
    """Return a one-line summary for claim-like event payloads."""
    owner = _text(payload.get("owner", "?")) or "?"
    status = _text(payload.get("status", "claimed")) or "claimed"
    raw_paths = payload.get("paths", ())
    paths_count = len(raw_paths) if isinstance(raw_paths, list | tuple) else 0
    parts = [f"{action} by {owner}", f"status={status}", f"paths={paths_count}"]
    raw_git = payload.get("git")
    if isinstance(raw_git, dict):
        branch = _text(raw_git.get("branch", ""))
        base = _text(raw_git.get("base", ""))
        if branch:
            parts.append(f"branch={branch}")
        if base:
            parts.append(f"base={base}")
    checkpoint = _text(payload.get("checkpoint", ""))
    if checkpoint:
        parts.append(f"checkpoint={checkpoint}")
    return "; ".join(parts)


def _ledger_task_summary(payload: dict[str, Any]) -> str:
    """Return a one-line summary for a board task snapshot."""
    title = _text(payload.get("title", ""))
    status = _text(payload.get("status", "open")) or "open"
    owner = _text(payload.get("suggested_owner", ""))
    parts = [title or "(untitled)", f"status={status}"]
    if owner:
        parts.append(f"suggested_owner={owner}")
    return "; ".join(parts)


def _progress_summary(payload: dict[str, Any]) -> str:
    """Return a one-line summary for a board progress note."""
    author = _text(payload.get("author", "?")) or "?"
    kind = _text(payload.get("kind", "note")) or "note"
    text = _text(payload.get("text", ""))
    return f"{kind} by {author}: {text}"


def _resource_summary(payload: dict[str, Any]) -> str:
    """Return a one-line summary for a resource-offer event."""
    agent = _text(payload.get("agent", "?")) or "?"
    kind = _text(payload.get("kind", "?")) or "?"
    name = _text(payload.get("name", "?")) or "?"
    capacity = _text(payload.get("capacity", "1")) or "1"
    return f"{agent} offers {kind}/{name}; capacity={capacity}"


def _corrupt_summary(event: StoredEvent) -> str:
    """Return the safe digest/reasons summary for one quarantined row marker."""
    marker = CorruptEventRow.from_payload(event.seq, event.payload)
    reasons = ",".join(reason.value for reason in marker.reasons)
    parts = [f"reasons={reasons}", f"payload_sha256={marker.payload_sha256}"]
    if marker.original_kind is not None:
        parts.insert(0, f"original_kind={marker.original_kind}")
    return "; ".join(parts)


def _event_summary(event: StoredEvent) -> tuple[str, str]:
    """Return ``(task_id, summary)`` for one coordination event."""
    payload = event.payload
    if event.kind == EventKind.CLAIM:
        return _event_task(payload), _claim_summary(payload, "claimed")
    if event.kind == EventKind.TASK_UPDATE:
        return _event_task(payload), _claim_summary(payload, "updated")
    if event.kind == EventKind.CHECKPOINT:
        return _event_task(payload), _claim_summary(payload, "checkpointed")
    if event.kind == EventKind.HANDOFF:
        return _event_task(payload), _claim_summary(payload, "handed off")
    if event.kind == EventKind.RELEASE:
        return _event_task(payload), "released"
    if event.kind == EventKind.LEDGER_TASK:
        return _event_task(payload), _ledger_task_summary(payload)
    if event.kind == EventKind.LEDGER_PROGRESS:
        return _event_task(payload), _progress_summary(payload)
    if event.kind == EventKind.RESOURCE:
        return "", _resource_summary(payload)
    if event.kind == EventKind.CORRUPT:
        return "", _corrupt_summary(event)
    return _event_task(payload), ""


def _latest(items: Iterable[StoredEvent], limit: int) -> tuple[list[StoredEvent], int]:
    """Return the latest ``limit`` events in chronological order and the full count."""
    all_items = list(items)
    if len(all_items) <= limit:
        return all_items, len(all_items)
    return all_items[-limit:], len(all_items)


def _html_table(headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
    """Render a small escaped HTML table."""
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(cell)}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")
    if not body_rows:
        col_count = len(headers)
        body_rows.append(f'<tr><td colspan="{col_count}">No entries.</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _event_count_rows(events: Iterable[StoredEvent]) -> list[tuple[str, str]]:
    """Return event-kind count rows sorted by kind."""
    counts: Counter[str] = Counter(event.kind for event in events)
    return [(kind, str(counts[kind])) for kind in sorted(counts)]


def _task_rows(events: Iterable[StoredEvent], limit: int) -> tuple[list[tuple[str, ...]], int]:
    """Return latest board task snapshot rows and the total task count."""
    by_task: dict[str, StoredEvent] = {}
    for event in events:
        if event.kind == EventKind.LEDGER_TASK:
            task_id = _event_task(event.payload)
            if task_id:
                by_task[task_id] = event
    rows: list[tuple[str, ...]] = []
    for task_id in sorted(by_task)[-limit:]:
        event = by_task[task_id]
        payload = event.payload
        rows.append(
            (
                task_id,
                _text(payload.get("status", "open")) or "open",
                _text(payload.get("title", "")),
                _text(payload.get("suggested_owner", "")),
                _stamp(event.ts),
            )
        )
    return rows, len(by_task)


def _receipt_events(events: Iterable[StoredEvent]) -> list[StoredEvent]:
    """Return progress events whose text contains a release receipt note."""
    receipts: list[StoredEvent] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS:
            continue
        text = _text(event.payload.get("text", ""))
        if text.startswith(RELEASE_RECEIPT_PREFIX):
            receipts.append(event)
    return receipts


def _timeline_rows(events: Iterable[StoredEvent], limit: int) -> tuple[list[tuple[str, ...]], int]:
    """Return bounded coordination timeline rows and the full timeline count."""
    timeline = [event for event in events if event.kind not in TIMELINE_EXCLUDED_KINDS]
    latest, total = _latest(timeline, limit)
    rows: list[tuple[str, ...]] = []
    for event in latest:
        task_id, summary = _event_summary(event)
        rows.append((str(event.seq), _stamp(event.ts), event.kind, task_id, summary))
    return rows, total


def _bounded_section_note(label: str, shown: int, total: int, limit: int) -> str:
    """Return a truncation note for a bounded report section."""
    if total <= shown:
        return ""
    return f'<p class="note">showing latest {limit} of {total} {escape(label)} event(s)</p>'


def _render_section(title: str, body: str) -> str:
    """Render one named report section."""
    return f"<section><h2>{escape(title)}</h2>{body}</section>"


def _with_document(body: str) -> str:
    """Wrap report body HTML in a complete static document."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>SYNAPSE archive report</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "line-height:1.45;margin:2rem;max-width:1100px;color:#202124;background:#fff}"
        "h1{font-size:1.8rem;margin-bottom:.25rem}h2{font-size:1.2rem;margin-top:2rem}"
        "dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem}"
        "dt{font-weight:700}dd{margin:0}table{border-collapse:collapse;width:100%;"
        "margin-top:.75rem}th,td{border:1px solid #d0d7de;padding:.4rem .5rem;"
        "text-align:left;vertical-align:top}th{background:#f6f8fa}.note{color:#57606a}"
        "</style></head><body>"
        f"{body}"
        "</body></html>"
    )


def render_archive_report(
    events: Iterable[StoredEvent],
    *,
    result: CompactionResult,
    options: ArchiveReportOptions,
) -> str:
    """Render an HTML archive report for a pre-compaction event snapshot.

    Parameters
    ----------
    events : Iterable[StoredEvent]
        Events read from the store before compaction runs.
    result : CompactionResult
        Actual compaction outcome to record in the report.
    options : ArchiveReportOptions
        Source path, timestamp, and bounded-section size.

    Returns
    -------
    str
        Complete static HTML document.
    """
    snapshot = list(events)
    limit = options.bounded_max_items
    event_count_rows = _event_count_rows(snapshot)
    receipt_rows, total_receipts = _timeline_rows(_receipt_events(snapshot), limit)
    task_rows, total_tasks = _task_rows(snapshot, limit)
    timeline_rows, total_timeline = _timeline_rows(snapshot, limit)

    body = (
        "<h1>SYNAPSE archive report</h1>"
        "<dl>"
        f"<dt>Source event store</dt><dd>{escape(options.source_path)}</dd>"
        f"<dt>Generated at</dt><dd>{escape(_stamp(options.generated_at))}</dd>"
        f"<dt>Total events before compaction</dt><dd>{len(snapshot)}</dd>"
        f"<dt>Compaction floor</dt><dd>{result.floor_seq}</dd>"
        f"<dt>{'Compaction result' if options.compaction_completed else 'Planned compaction'}</dt>"
        f"<dd>removed {result.checkpoints_removed} checkpoint(s), "
        f"{result.findings_removed} finding(s), "
        f"{result.corrupt_rows_removed} corrupt row(s)</dd>"
        "</dl>"
    )
    body += _render_section(
        "Event counts",
        _html_table(("Kind", "Count"), event_count_rows),
    )
    body += _render_section(
        "Release receipts",
        _html_table(("Seq", "Time", "Kind", "Task", "Summary"), receipt_rows)
        + _bounded_section_note("release receipt", len(receipt_rows), total_receipts, limit),
    )
    body += _render_section(
        "Board tasks",
        _html_table(("Task", "Status", "Title", "Suggested owner", "Last update"), task_rows)
        + _bounded_section_note("board task", len(task_rows), total_tasks, limit),
    )
    body += _render_section(
        "Coordination timeline",
        _html_table(("Seq", "Time", "Kind", "Task", "Summary"), timeline_rows)
        + _bounded_section_note("coordination", len(timeline_rows), total_timeline, limit),
    )
    return _with_document(body)


def write_archive_report(path: str | Path, html_text: str) -> None:
    """Atomically write an archive report with owner-only permissions.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination HTML file. Parent directories are created.
    html_text : str
        Complete report body to write.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f"{destination.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with contextlib.suppress(OSError):
            tmp_path.chmod(REPORT_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, destination)
        os.chmod(destination, REPORT_FILE_MODE)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
