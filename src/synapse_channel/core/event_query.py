# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — temporal event-log query language
"""Query the durable hub event log for temporal coordination evidence.

The query language is deliberately small and read-only. It opens an existing
SQLite event store, filters persisted events, and reconstructs task or conflict
state at a requested sequence or timestamp without contacting a live hub.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.delivery_receipts import (
    DELIVERY_RECEIPT_EVENT_KINDS,
    format_receipt_event,
    receipt_event_matches,
    receipt_event_to_json,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.universal_receipts import (
    UNIVERSAL_RECEIPT_EVENT_KINDS,
    UniversalReceipt,
    format_universal_receipt,
    universal_receipt_matches,
    universal_receipt_to_json,
    universal_receipts_from_events,
)
from synapse_channel.terminal_text import terminal_text

TASK_EVENT_KINDS = frozenset(
    {
        EventKind.CLAIM,
        EventKind.TASK_UPDATE,
        EventKind.CHECKPOINT,
        EventKind.HANDOFF,
        EventKind.RELEASE,
    }
)
"""Event kinds that can affect a task timeline or live task state."""

CLAIM_SNAPSHOT_KINDS = frozenset(
    {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}
)
"""Event kinds whose payload is a full task-claim snapshot."""

_ATOM_VALUE = r"(?P<{name}>\"[^\"]+\"|'[^']+'|[^,\s)]+)"
_CYPHER_TASK_TIMELINE_RE = re.compile(
    r"^MATCH\s+\(task:TASK\s+\{id:(?P<quote>[\"'])(?P<task>[^\"']+)(?P=quote)\}\)"
    r"\s+RETURN\s+timeline$",
    re.IGNORECASE,
)
_CYPHER_TASK_STATE_RE = re.compile(
    r"^MATCH\s+\(task:TASK\s+\{id:(?P<quote>[\"'])(?P<task>[^\"']+)(?P=quote)\}\)"
    r"\s+AT\s+(?P<cutoff_kind>seq|time)\s+(?P<cutoff>\S+)"
    r"\s+RETURN\s+state$",
    re.IGNORECASE,
)
_CYPHER_PATH_TOUCHED_RE = re.compile(
    r"^MATCH\s+\(path:PATH\s+\{value:(?P<quote>[\"'])(?P<path>[^\"']+)(?P=quote)\}\)"
    r"\s+BETWEEN\s+(?P<lower>\S+)\s+(?P<upper>\S+)"
    r"\s+RETURN\s+events$",
    re.IGNORECASE,
)
_CYPHER_CONFLICTS_RE = re.compile(
    r"^MATCH\s+\(conflicts\)\s+AT\s+(?P<cutoff_kind>seq|time)\s+"
    r"(?P<cutoff>\S+)\s+RETURN\s+pairs$",
    re.IGNORECASE,
)
_DATALOG_TASK_TIMELINE_RE = re.compile(
    rf"^timeline\(\s*{_ATOM_VALUE.format(name='task')}\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_TASK_STATE_RE = re.compile(
    rf"^state\(\s*{_ATOM_VALUE.format(name='task')}\s*,\s*"
    r"(?P<cutoff_kind>seq|time)\s*,\s*(?P<cutoff>[^,\s)]+)\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_PATH_TOUCHED_RE = re.compile(
    rf"^touches\(\s*{_ATOM_VALUE.format(name='path')}\s*,\s*"
    r"(?P<lower>[^,\s)]+)\s*,\s*(?P<upper>[^,\s)]+)\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_CONFLICTS_RE = re.compile(
    r"^conflicts\(\s*(?P<cutoff_kind>seq|time)\s*,\s*"
    r"(?P<cutoff>[^,\s)]+)\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_CHANNEL_RE = re.compile(
    rf"^channel\(\s*{_ATOM_VALUE.format(name='channel')}\s*,\s*"
    r"(?P<cutoff_kind>seq|time)\s*,\s*(?P<lower>[^,\s)]+)\s*,\s*"
    r"(?P<upper>[^,\s)]+)\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_RECEIPTS_RE = re.compile(
    rf"^receipts\(\s*{_ATOM_VALUE.format(name='participant')}\s*\)\.?$",
    re.IGNORECASE,
)
_DATALOG_UNIVERSAL_RECEIPTS_RE = re.compile(
    rf"^universal_receipts\(\s*{_ATOM_VALUE.format(name='participant')}\s*\)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EventQuery:
    """Parsed event-log query.

    Attributes
    ----------
    kind : str
        Query kind: ``task_timeline``, ``task_state``, ``path_touched``,
        ``channel_events``, or ``conflicts``.
    task_id : str
        Task id for task-scoped queries.
    path : str
        Path for path-touch queries.
    channel_id : str
        Private-channel id for channel-event queries.
    participant : str
        Agent, target, or ``all`` selector for delivery-receipt ledger queries.
    lower : float
        Inclusive lower timestamp for path-touch queries.
    upper : float
        Inclusive upper timestamp for path-touch queries.
    cutoff_kind : str
        ``seq`` or ``time`` for point-in-time queries.
    cutoff : float
        Sequence or timestamp cutoff for point-in-time queries.
    raw : str
        Original query text.
    """

    kind: str
    task_id: str = ""
    path: str = ""
    channel_id: str = ""
    participant: str = ""
    lower: float = 0.0
    upper: float = 0.0
    cutoff_kind: str = ""
    cutoff: float = 0.0
    raw: str = ""


@dataclass(frozen=True)
class QueryRecord:
    """One event selected by an event-log query."""

    event: StoredEvent
    task_id: str
    owner: str
    status: str
    worktree: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    """Result returned by one event-log query."""

    kind: str
    query: str
    records: tuple[QueryRecord, ...] = ()
    state: dict[str, object] | None = None
    conflicts: list[dict[str, object]] | None = None
    receipt_events: tuple[StoredEvent, ...] = ()
    universal_receipts: tuple[UniversalReceipt, ...] = ()


def parse_query(query: str) -> EventQuery:
    """Parse one temporal event-log query string.

    Parameters
    ----------
    query : str
        Query text. Supported canonical forms are ``task <id> timeline``,
        ``task <id> at seq <n>``, ``task <id> at time <seconds>``,
        ``path <path> between <start> <end>``,
        ``channel <id> between seq|time <start> <end>``, and
        ``conflicts at seq|time <n>``.
        The parser also accepts tiny Datalog-like aliases such as
        ``timeline("TASK").`` and Cypher-like aliases such as
        ``MATCH (task:TASK {id:"TASK"}) RETURN timeline``.

    Returns
    -------
    EventQuery
        Parsed query object.

    Raises
    ------
    ValueError
        If the query does not match a supported form or a numeric field is
        invalid.
    """
    tokens = query.split()
    if len(tokens) == 3 and tokens[0] == "task" and tokens[2] == "timeline":
        return EventQuery(kind="task_timeline", task_id=tokens[1], raw=query)
    if len(tokens) == 5 and tokens[0] == "task" and tokens[2] == "at":
        cutoff_kind = _parse_cutoff_kind(tokens[3])
        return EventQuery(
            kind="task_state",
            task_id=tokens[1],
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, tokens[4]),
            raw=query,
        )
    if len(tokens) == 5 and tokens[0] == "path" and tokens[2] == "between":
        return EventQuery(
            kind="path_touched",
            path=tokens[1],
            lower=_parse_float(tokens[3], "invalid lower timestamp"),
            upper=_parse_float(tokens[4], "invalid upper timestamp"),
            raw=query,
        )
    if len(tokens) == 6 and tokens[0] == "channel" and tokens[2] == "between":
        cutoff_kind = _parse_cutoff_kind(tokens[3])
        return EventQuery(
            kind="channel_events",
            channel_id=tokens[1],
            cutoff_kind=cutoff_kind,
            lower=_parse_cutoff_value(cutoff_kind, tokens[4]),
            upper=_parse_cutoff_value(cutoff_kind, tokens[5]),
            raw=query,
        )
    if len(tokens) == 4 and tokens[0] == "conflicts" and tokens[1] == "at":
        cutoff_kind = _parse_cutoff_kind(tokens[2])
        return EventQuery(
            kind="conflicts",
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, tokens[3]),
            raw=query,
        )
    if len(tokens) == 2 and tokens[0] == "receipts":
        return EventQuery(kind="delivery_receipts", participant=tokens[1], raw=query)
    if len(tokens) == 2 and tokens[0] == "universal-receipts":
        return EventQuery(kind="universal_receipts", participant=tokens[1], raw=query)
    parsed_alias = _parse_cypher_like_query(query)
    if parsed_alias is not None:
        return parsed_alias
    parsed_alias = _parse_datalog_like_query(query)
    if parsed_alias is not None:
        return parsed_alias
    msg = f"unsupported event query: {query}"
    raise ValueError(msg)


def run_query(
    db_path: str | Path,
    query: str,
    *,
    limit: int | None = None,
    key_file: str | Path | None = None,
) -> QueryResult:
    """Run one temporal event-log query against an existing SQLite store.

    The store is read selectively: only the sequence/time window and event kinds
    a query needs are loaded from SQLite, so a growing event log does not force a
    full scan for every query. The result is identical to scanning the whole
    store because the loaded window is always a superset of the events the query
    keeps.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    query : str
        Query text (see :func:`parse_query`).
    limit : int or None, optional
        When given, cap the result to its most recent ``limit`` records (and
        conflict pairs); ``None`` returns every match.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key when the event store is encrypted.

    Returns
    -------
    QueryResult
        The query result, optionally capped to ``limit`` records.

    Raises
    ------
    ValueError
        If the event store does not exist or the query is unsupported.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    parsed = parse_query(query)
    store = EventStore(path, key_file=key_file)
    try:
        events = tuple(store.read_window(**_selective_read_args(parsed)))
    finally:
        store.close()
    result = execute_query(events, parsed)
    if limit is not None:
        result = _cap_result(result, limit)
    return result


def _selective_read_args(query: EventQuery) -> dict[str, Any]:
    """Return :meth:`EventStore.read_window` bounds that load a sufficient subset.

    The window is a guaranteed superset of the events :func:`execute_query` keeps
    for ``query``, so a selective read yields results identical to a full scan.

    Raises
    ------
    ValueError
        If the query kind has no selective-read mapping.
    """
    if query.kind == "task_timeline":
        return {"kinds": TASK_EVENT_KINDS}
    if query.kind in {"task_state", "conflicts"}:
        return _cutoff_window(query.cutoff_kind, query.cutoff)
    if query.kind == "path_touched":
        return {"since_ts": query.lower, "until_ts": query.upper, "kinds": CLAIM_SNAPSHOT_KINDS}
    if query.kind == "channel_events":
        return {
            **_range_window(query.cutoff_kind, query.lower, query.upper),
            "kinds": (EventKind.CHAT,),
        }
    if query.kind == "delivery_receipts":
        return {"kinds": DELIVERY_RECEIPT_EVENT_KINDS}
    if query.kind == "universal_receipts":
        return {"kinds": UNIVERSAL_RECEIPT_EVENT_KINDS}
    msg = f"unsupported event query kind: {query.kind}"
    raise ValueError(msg)


def _cutoff_window(cutoff_kind: str, cutoff: float) -> dict[str, Any]:
    """Return an upper-bounded window for an at-or-before cutoff."""
    if cutoff_kind == "seq":
        return {"max_seq": int(cutoff)}
    return {"until_ts": cutoff}


def _range_window(cutoff_kind: str, lower: float, upper: float) -> dict[str, Any]:
    """Return an inclusive window for a sequence or time range."""
    if cutoff_kind == "seq":
        return {"min_seq": int(lower), "max_seq": int(upper)}
    return {"since_ts": lower, "until_ts": upper}


def _cap_result(result: QueryResult, limit: int) -> QueryResult:
    """Return ``result`` with records and conflicts capped to the last ``limit``."""
    keep = max(0, int(limit))
    records = tuple(result.records[-keep:]) if keep else ()
    if result.conflicts is None:
        conflicts = None
    else:
        conflicts = list(result.conflicts[-keep:]) if keep else []
    return QueryResult(
        kind=result.kind,
        query=result.query,
        records=records,
        state=result.state,
        conflicts=conflicts,
        receipt_events=tuple(result.receipt_events[-keep:]) if keep else (),
        universal_receipts=tuple(result.universal_receipts[-keep:]) if keep else (),
    )


def execute_query(events: Sequence[StoredEvent], query: EventQuery) -> QueryResult:
    """Execute a parsed query against already-loaded events."""
    if query.kind == "task_timeline":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            records=tuple(
                _record_from_event(event)
                for event in events
                if _event_task_id(event) == query.task_id and event.kind in TASK_EVENT_KINDS
            ),
        )
    if query.kind == "task_state":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            state=_task_state_at(events, query.task_id, query.cutoff_kind, query.cutoff),
        )
    if query.kind == "path_touched":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            records=tuple(
                record
                for event in events
                if query.lower <= event.ts <= query.upper
                for record in (_record_from_event(event),)
                if event.kind in CLAIM_SNAPSHOT_KINDS and _paths_overlap(query.path, record.paths)
            ),
        )
    if query.kind == "conflicts":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            conflicts=_conflicts_at(events, query.cutoff_kind, query.cutoff),
        )
    if query.kind == "channel_events":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            records=tuple(
                _record_from_event(event)
                for event in events
                if event.kind == EventKind.CHAT
                and str(event.payload.get("channel") or "") == query.channel_id
                and _event_is_in_range(event, query.cutoff_kind, query.lower, query.upper)
            ),
        )
    if query.kind == "delivery_receipts":
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            receipt_events=tuple(
                event for event in events if receipt_event_matches(event, query.participant)
            ),
        )
    if query.kind == "universal_receipts":
        receipts = universal_receipts_from_events(events)
        return QueryResult(
            kind=query.kind,
            query=query.raw,
            universal_receipts=tuple(
                receipt
                for receipt in receipts
                if universal_receipt_matches(receipt, query.participant)
            ),
        )
    msg = f"unsupported event query kind: {query.kind}"
    raise ValueError(msg)


def result_to_json(result: QueryResult) -> dict[str, object]:
    """Convert a query result into a stable JSON-compatible object."""
    payload: dict[str, object] = {"kind": result.kind, "query": result.query}
    if result.records:
        payload["records"] = (
            [_channel_record_to_json(record) for record in result.records]
            if result.kind == "channel_events"
            else [_record_to_json(record) for record in result.records]
        )
    if result.state is not None:
        payload["state"] = result.state
    if result.conflicts is not None:
        payload["conflicts"] = [dict(conflict) for conflict in result.conflicts]
    if result.receipt_events:
        payload["receipts"] = [receipt_event_to_json(event) for event in result.receipt_events]
    if result.universal_receipts:
        payload["receipts"] = [
            universal_receipt_to_json(receipt) for receipt in result.universal_receipts
        ]
    return payload


def render_human(result: QueryResult) -> str:
    """Render a query result as compact terminal text."""
    if result.kind == "task_timeline":
        task = _query_task_label(result.query)
        lines = [f"task {terminal_text(task)} timeline: {len(result.records)} event(s)"]
        lines.extend(_format_record(record) for record in result.records)
        return "\n".join(lines)
    if result.kind == "task_state":
        if not result.state:
            return "task state: not found"
        return (
            f"task {terminal_text(result.state['task_id'])} state: "
            f"owner={terminal_text(result.state['owner'])} "
            f"status={terminal_text(result.state['status'])} seq={result.state['event_seq']}"
        )
    if result.kind == "path_touched":
        lines = [f"path touched: {len(result.records)} event(s)"]
        lines.extend(_format_record(record) for record in result.records)
        return "\n".join(lines)
    if result.kind == "conflicts":
        conflicts = [] if result.conflicts is None else result.conflicts
        lines = [f"conflicts: {len(conflicts)} pair(s)"]
        lines.extend(_format_conflict(item) for item in conflicts)
        return "\n".join(lines)
    if result.kind == "channel_events":
        channel = _query_channel_label(result.query)
        lines = [f"channel {terminal_text(channel)}: {len(result.records)} event(s)"]
        lines.extend(_format_channel_record(record) for record in result.records)
        return "\n".join(lines)
    if result.kind == "delivery_receipts":
        participant = _query_receipt_label(result.query)
        lines = [
            f"delivery receipts {terminal_text(participant)}: {len(result.receipt_events)} event(s)"
        ]
        lines.extend(terminal_text(format_receipt_event(event)) for event in result.receipt_events)
        return "\n".join(lines)
    if result.kind == "universal_receipts":
        participant = _query_receipt_label(result.query)
        lines = [
            f"universal receipts {terminal_text(participant)}: "
            f"{len(result.universal_receipts)} item(s)"
        ]
        lines.extend(
            terminal_text(format_universal_receipt(receipt))
            for receipt in result.universal_receipts
        )
        return "\n".join(lines)
    return f"{result.kind}: no renderer"


def _parse_cutoff_kind(value: str) -> str:
    """Validate a point-in-time cutoff kind."""
    normalized = value.lower()
    if normalized in {"seq", "time"}:
        return normalized
    msg = f"invalid cutoff kind: {value}"
    raise ValueError(msg)


def _parse_cutoff_value(kind: str, value: str) -> float:
    """Parse a sequence or timestamp cutoff value."""
    if kind == "seq":
        try:
            return float(int(value))
        except ValueError as exc:
            msg = f"invalid sequence: {value}"
            raise ValueError(msg) from exc
    return _parse_float(value, "invalid timestamp")


def _parse_cypher_like_query(query: str) -> EventQuery | None:
    """Parse supported Cypher-like aliases into the existing query model."""
    task_timeline = _CYPHER_TASK_TIMELINE_RE.match(query)
    if task_timeline is not None:
        return EventQuery(
            kind="task_timeline",
            task_id=task_timeline.group("task"),
            raw=query,
        )

    task_state = _CYPHER_TASK_STATE_RE.match(query)
    if task_state is not None:
        cutoff_kind = _parse_cutoff_kind(task_state.group("cutoff_kind"))
        return EventQuery(
            kind="task_state",
            task_id=task_state.group("task"),
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, task_state.group("cutoff")),
            raw=query,
        )

    path_touched = _CYPHER_PATH_TOUCHED_RE.match(query)
    if path_touched is not None:
        return EventQuery(
            kind="path_touched",
            path=path_touched.group("path"),
            lower=_parse_float(path_touched.group("lower"), "invalid lower timestamp"),
            upper=_parse_float(path_touched.group("upper"), "invalid upper timestamp"),
            raw=query,
        )

    conflicts = _CYPHER_CONFLICTS_RE.match(query)
    if conflicts is not None:
        cutoff_kind = _parse_cutoff_kind(conflicts.group("cutoff_kind"))
        return EventQuery(
            kind="conflicts",
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, conflicts.group("cutoff")),
            raw=query,
        )
    return None


def _parse_datalog_like_query(query: str) -> EventQuery | None:
    """Parse supported Datalog-like aliases into the existing query model."""
    task_timeline = _DATALOG_TASK_TIMELINE_RE.match(query)
    if task_timeline is not None:
        return EventQuery(
            kind="task_timeline",
            task_id=_unquote_atom(task_timeline.group("task")),
            raw=query,
        )

    task_state = _DATALOG_TASK_STATE_RE.match(query)
    if task_state is not None:
        cutoff_kind = _parse_cutoff_kind(task_state.group("cutoff_kind"))
        return EventQuery(
            kind="task_state",
            task_id=_unquote_atom(task_state.group("task")),
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, task_state.group("cutoff")),
            raw=query,
        )

    path_touched = _DATALOG_PATH_TOUCHED_RE.match(query)
    if path_touched is not None:
        return EventQuery(
            kind="path_touched",
            path=_unquote_atom(path_touched.group("path")),
            lower=_parse_float(path_touched.group("lower"), "invalid lower timestamp"),
            upper=_parse_float(path_touched.group("upper"), "invalid upper timestamp"),
            raw=query,
        )

    conflicts = _DATALOG_CONFLICTS_RE.match(query)
    if conflicts is not None:
        cutoff_kind = _parse_cutoff_kind(conflicts.group("cutoff_kind"))
        return EventQuery(
            kind="conflicts",
            cutoff_kind=cutoff_kind,
            cutoff=_parse_cutoff_value(cutoff_kind, conflicts.group("cutoff")),
            raw=query,
        )
    channel = _DATALOG_CHANNEL_RE.match(query)
    if channel is not None:
        cutoff_kind = _parse_cutoff_kind(channel.group("cutoff_kind"))
        return EventQuery(
            kind="channel_events",
            channel_id=_unquote_atom(channel.group("channel")),
            cutoff_kind=cutoff_kind,
            lower=_parse_cutoff_value(cutoff_kind, channel.group("lower")),
            upper=_parse_cutoff_value(cutoff_kind, channel.group("upper")),
            raw=query,
        )
    receipts = _DATALOG_RECEIPTS_RE.match(query)
    if receipts is not None:
        return EventQuery(
            kind="delivery_receipts",
            participant=_unquote_atom(receipts.group("participant")),
            raw=query,
        )
    universal_receipts = _DATALOG_UNIVERSAL_RECEIPTS_RE.match(query)
    if universal_receipts is not None:
        return EventQuery(
            kind="universal_receipts",
            participant=_unquote_atom(universal_receipts.group("participant")),
            raw=query,
        )
    return None


def _unquote_atom(value: str) -> str:
    """Remove optional single or double quotes from a Datalog-style atom."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_float(value: str, message: str) -> float:
    """Parse a float value or raise a labelled ``ValueError``."""
    try:
        return float(value)
    except ValueError as exc:
        msg = f"{message}: {value}"
        raise ValueError(msg) from exc


def _record_from_event(event: StoredEvent) -> QueryRecord:
    """Project a stored event into the query-record shape."""
    payload = event.payload
    return QueryRecord(
        event=event,
        task_id=_event_task_id(event),
        owner=str(payload.get("owner", "")),
        status=str(payload.get("status", "")),
        worktree=str(payload.get("worktree", "")),
        paths=tuple(str(path) for path in payload.get("paths", ())),
    )


def _event_task_id(event: StoredEvent) -> str:
    """Return the task id carried by an event payload, if any."""
    value = event.payload.get("task_id", "")
    return str(value)


def _task_state_at(
    events: Sequence[StoredEvent],
    task_id: str,
    cutoff_kind: str,
    cutoff: float,
) -> dict[str, object]:
    """Reconstruct one task's latest state at a sequence or timestamp cutoff."""
    state: dict[str, object] | None = None
    for event in events:
        if not _event_is_at_or_before(event, cutoff_kind, cutoff):
            continue
        if _event_task_id(event) != task_id:
            continue
        if event.kind == EventKind.RELEASE:
            state = None
            continue
        if event.kind in CLAIM_SNAPSHOT_KINDS:
            state = _state_from_snapshot(event)
    return {} if state is None else state


def _state_from_snapshot(event: StoredEvent) -> dict[str, object]:
    """Return the public task-state fields from a claim snapshot event."""
    payload = event.payload
    return {
        "task_id": str(payload.get("task_id", "")),
        "owner": str(payload.get("owner", "")),
        "status": str(payload.get("status", "")),
        "data_ref": str(payload.get("data_ref", "")),
        "paths": [str(path) for path in payload.get("paths", ())],
        "worktree": str(payload.get("worktree", "")),
        "event_seq": event.seq,
        "event_ts": event.ts,
    }


def _conflicts_at(
    events: Sequence[StoredEvent],
    cutoff_kind: str,
    cutoff: float,
) -> list[dict[str, object]]:
    """Return path-overlap conflicts among live claims at a cutoff."""
    live: dict[str, QueryRecord] = {}
    for event in events:
        if not _event_is_at_or_before(event, cutoff_kind, cutoff):
            continue
        task_id = _event_task_id(event)
        if not task_id:
            continue
        if event.kind == EventKind.RELEASE:
            live.pop(task_id, None)
            continue
        if event.kind in CLAIM_SNAPSHOT_KINDS:
            live[task_id] = _record_from_event(event)

    conflicts: list[dict[str, object]] = []
    records = tuple(live[key] for key in sorted(live))
    for left_index, left in enumerate(records):
        for right in records[left_index + 1 :]:
            if left.owner == right.owner or left.worktree != right.worktree:
                continue
            if _paths_overlap_many(left.paths, right.paths):
                conflicts.append(
                    {
                        "left_task": left.task_id,
                        "left_owner": left.owner,
                        "right_task": right.task_id,
                        "right_owner": right.owner,
                        "worktree": left.worktree,
                        "paths": list(_unique_ordered((*left.paths, *right.paths))),
                    }
                )
    return conflicts


def _event_is_at_or_before(event: StoredEvent, cutoff_kind: str, cutoff: float) -> bool:
    """Return whether an event is visible at the requested cutoff."""
    if cutoff_kind == "seq":
        return event.seq <= int(cutoff)
    return event.ts <= cutoff


def _event_is_in_range(event: StoredEvent, cutoff_kind: str, lower: float, upper: float) -> bool:
    """Return whether an event falls inside an inclusive sequence or time range."""
    value = float(event.seq) if cutoff_kind == "seq" else event.ts
    return lower <= value <= upper


def _paths_overlap(path: str, scopes: Sequence[str]) -> bool:
    """Return whether ``path`` overlaps any declared path scope."""
    if not scopes:
        return True
    return any(_path_pair_overlaps(path, scope) for scope in scopes)


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
    """Return values without duplicates while preserving order."""
    return tuple(dict.fromkeys(values))


def _record_to_json(record: QueryRecord) -> dict[str, object]:
    """Convert a selected event record into JSON-compatible fields."""
    return {
        "seq": record.event.seq,
        "ts": record.event.ts,
        "kind": record.event.kind,
        "task_id": record.task_id,
        "owner": record.owner,
        "status": record.status,
        "worktree": record.worktree,
        "paths": list(record.paths),
        "payload": record.event.payload,
    }


def _channel_record_to_json(record: QueryRecord) -> dict[str, object]:
    """Convert a channel chat event into metadata-only JSON fields."""
    payload = record.event.payload
    body = str(payload.get("payload") or "")
    return {
        "seq": record.event.seq,
        "ts": record.event.ts,
        "kind": record.event.kind,
        "channel": str(payload.get("channel") or ""),
        "sender": str(payload.get("sender") or ""),
        "target": str(payload.get("target") or "all"),
        "msg_id": int(payload.get("msg_id", 0)),
        "payload_bytes": len(body.encode("utf-8")),
    }


def _format_record(record: QueryRecord) -> str:
    """Render one selected event record."""
    return (
        f"- seq={record.event.seq} ts={record.event.ts:.3f} "
        f"kind={terminal_text(record.event.kind)} task={terminal_text(record.task_id)} "
        f"owner={terminal_text(record.owner)} status={terminal_text(record.status)}"
    )


def _format_channel_record(record: QueryRecord) -> str:
    """Render one channel event without its payload body."""
    payload = record.event.payload
    return (
        f"- seq={record.event.seq} {terminal_text(record.event.kind)} "
        f"{terminal_text(payload.get('sender', '?'))} "
        f"channel={terminal_text(payload.get('channel', ''))}"
    )


def _format_conflict(item: dict[str, object]) -> str:
    """Render one reconstructed conflict pair."""
    raw_paths = item.get("paths", ())
    if isinstance(raw_paths, (list, tuple)):
        path_text = ",".join(terminal_text(path) for path in raw_paths)
    else:
        path_text = terminal_text(raw_paths)
    return (
        f"- {terminal_text(item['left_task'])}@{terminal_text(item['left_owner'])} <-> "
        f"{terminal_text(item['right_task'])}@{terminal_text(item['right_owner'])} "
        f"paths={path_text}"
    )


def _query_task_label(query: str) -> str:
    """Return the task label from a supported task query string."""
    try:
        parsed = parse_query(query)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.task_id:
        return parsed.task_id
    tokens = query.split()
    return tokens[1] if len(tokens) >= 2 else "?"


def _query_channel_label(query: str) -> str:
    """Return the channel label from a supported channel query string."""
    try:
        parsed = parse_query(query)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.channel_id:
        return parsed.channel_id
    tokens = query.split()
    return tokens[1] if len(tokens) >= 2 else "?"


def _query_receipt_label(query: str) -> str:
    """Return the participant label from a supported receipt query string."""
    try:
        parsed = parse_query(query)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.participant:
        return parsed.participant
    tokens = query.split()
    return tokens[1] if len(tokens) >= 2 else "?"
