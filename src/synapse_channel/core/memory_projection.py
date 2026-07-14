# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic local memory projection and recall
"""Deterministic, local memory projection over durable event-store records.

The projection is intentionally simple: it tokenises durable memory atoms from
the event log, scores query overlap, and returns recall hits with the original
sequence, timestamp, event kind, source field, task id, actor, and evidence
reference. It does not create embeddings, call external services, or mutate hub
state; the event store remains the provenance boundary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.journal import MEMORY_KINDS, EventKind
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.terminal_text import terminal_text

MEMORY_RECALL_TRUST_BOUNDARY = (
    "Memory recall is deterministic local projection over durable event-log records; "
    "it does not create embeddings, contact external services, certify truth, or mutate hub state."
)
"""Boundary statement included in every memory recall report."""

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


class MemoryRecallInputError(SynapseError, ValueError):
    """Raised when a recall request cannot read the requested event store."""

    code = "memory_recall_input"


@dataclass(frozen=True)
class MemoryProjectionRecord:
    """One searchable projection of a durable memory event.

    Attributes
    ----------
    seq : int
        Event-store sequence of the source event.
    ts : float
        Event timestamp in seconds.
    kind : str
        Source event kind.
    source : str
        Payload field used as the text source.
    text : str
        Text projected for recall.
    tokens : tuple[str, ...]
        Stable unique tokens extracted from ``text``.
    task_id : str
        Task id carried by the source event, when present.
    actor : str
        Actor, owner, or handoff source carried by the source event, when present.
    evidence_ref : str
        Evidence reference carried by the source event, when present.
    """

    seq: int
    ts: float
    kind: str
    source: str
    text: str
    tokens: tuple[str, ...]
    task_id: str
    actor: str
    evidence_ref: str


@dataclass(frozen=True)
class MemoryRecallHit:
    """One ranked recall hit with provenance and match explanation."""

    seq: int
    ts: float
    kind: str
    source: str
    text: str
    task_id: str
    actor: str
    evidence_ref: str
    score: float
    matched_tokens: tuple[str, ...]


@dataclass(frozen=True)
class MemoryRecallReport:
    """Complete result of one local memory recall query."""

    query: str
    query_tokens: tuple[str, ...]
    since_seq: int
    limit: int
    trust_boundary: str
    hits: tuple[MemoryRecallHit, ...]


def project_memory_events(
    events: list[StoredEvent] | tuple[StoredEvent, ...],
) -> tuple[MemoryProjectionRecord, ...]:
    """Project durable memory events into searchable local records.

    Parameters
    ----------
    events : list[StoredEvent] or tuple[StoredEvent, ...]
        Event-store records to project.

    Returns
    -------
    tuple[MemoryProjectionRecord, ...]
        Searchable records for findings, checkpoints, and handoffs. Recall query
        telemetry and non-memory events are ignored.
    """
    records: list[MemoryProjectionRecord] = []
    for event in events:
        if event.kind == EventKind.RECALL or event.kind not in MEMORY_KINDS:
            continue
        text, source = _event_text(event)
        tokens = _tokens(text)
        if not tokens:
            continue
        payload = event.payload
        records.append(
            MemoryProjectionRecord(
                seq=event.seq,
                ts=event.ts,
                kind=event.kind,
                source=source,
                text=text,
                tokens=tokens,
                task_id=_text_field(payload, "task_id"),
                actor=_actor(payload),
                evidence_ref=_text_field(payload, "evidence_ref"),
            )
        )
    return tuple(records)


def recall_memory(
    events: list[StoredEvent] | tuple[StoredEvent, ...],
    query: str,
    *,
    since_seq: int = 0,
    limit: int = 5,
) -> MemoryRecallReport:
    """Return ranked local recall hits for ``query``.

    Parameters
    ----------
    events : list[StoredEvent] or tuple[StoredEvent, ...]
        Event-store records to project and score.
    query : str
        Query text.
    since_seq : int, optional
        Cursor used to read the records. Preserved in the report for auditability.
    limit : int, optional
        Maximum hits to return. Non-positive limits return no hits.

    Returns
    -------
    MemoryRecallReport
        Deterministic recall report with matched-token explanations.
    """
    query_tokens = _tokens(query)
    safe_since_seq = safe_int(since_seq, default=0, min_value=0, allow_bool=False)
    safe_limit = safe_int(limit, default=0, min_value=0, allow_bool=False)
    if not query_tokens or safe_limit <= 0:
        return MemoryRecallReport(
            query=query,
            query_tokens=query_tokens,
            since_seq=safe_since_seq,
            limit=safe_limit,
            trust_boundary=MEMORY_RECALL_TRUST_BOUNDARY,
            hits=(),
        )
    scored: list[MemoryRecallHit] = []
    query_set = set(query_tokens)
    for record in project_memory_events(events):
        matched = tuple(token for token in query_tokens if token in record.tokens)
        if not matched:
            continue
        score = len(matched) / len(query_tokens)
        scored.append(
            MemoryRecallHit(
                seq=record.seq,
                ts=record.ts,
                kind=record.kind,
                source=record.source,
                text=record.text,
                task_id=record.task_id,
                actor=record.actor,
                evidence_ref=record.evidence_ref,
                score=score,
                matched_tokens=matched,
            )
        )
    scored.sort(key=lambda hit: (-hit.score, -len(set(hit.matched_tokens) & query_set), hit.seq))
    return MemoryRecallReport(
        query=query,
        query_tokens=query_tokens,
        since_seq=safe_since_seq,
        limit=safe_limit,
        trust_boundary=MEMORY_RECALL_TRUST_BOUNDARY,
        hits=tuple(scored[:safe_limit]),
    )


def read_memory_recall(
    db_path: str | Path,
    query: str,
    *,
    since_seq: int = 0,
    limit: int = 5,
    key_file: str | Path | None = None,
) -> MemoryRecallReport:
    """Read a local event store and return a deterministic recall report.

    Parameters
    ----------
    db_path : str or pathlib.Path
        SQLite event-store path.
    query : str
        Query text.
    since_seq : int, optional
        Exclusive lower event-store sequence bound.
    limit : int, optional
        Maximum hits to return.

    Returns
    -------
    MemoryRecallReport
        Recall report over memory event kinds.

    Raises
    ------
    MemoryRecallInputError
        When ``db_path`` does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        raise MemoryRecallInputError(f"missing event store: {path}")
    start = safe_int(since_seq, default=0, min_value=0, allow_bool=False)
    store = EventStore(path, key_file=key_file)
    try:
        events = store.read_since(start, kinds=MEMORY_KINDS)
    finally:
        store.close()
    return recall_memory(events, query, since_seq=start, limit=limit)


def memory_recall_to_json(report: MemoryRecallReport) -> str:
    """Serialise ``report`` as stable, indented JSON."""
    return json.dumps(
        {
            "query": report.query,
            "query_tokens": list(report.query_tokens),
            "since_seq": report.since_seq,
            "limit": report.limit,
            "trust_boundary": report.trust_boundary,
            "hits": [
                {
                    "seq": hit.seq,
                    "ts": hit.ts,
                    "kind": hit.kind,
                    "source": hit.source,
                    "text": hit.text,
                    "task_id": hit.task_id,
                    "actor": hit.actor,
                    "evidence_ref": hit.evidence_ref,
                    "score": hit.score,
                    "matched_tokens": list(hit.matched_tokens),
                }
                for hit in report.hits
            ],
        },
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )


def render_memory_recall(report: MemoryRecallReport) -> str:
    """Render ``report`` as a compact human-readable recall view."""
    lines = [
        f"Memory recall for: {terminal_text(report.query)}",
        "Query tokens: "
        + (
            ", ".join(terminal_text(token) for token in report.query_tokens)
            if report.query_tokens
            else "(none)"
        ),
        f"Since seq: {report.since_seq}",
        terminal_text(report.trust_boundary),
    ]
    if not report.hits:
        lines.append("No matching memory records.")
        return "\n".join(lines)
    for hit in report.hits:
        task = f" task={terminal_text(hit.task_id)}" if hit.task_id else ""
        actor = f" actor={terminal_text(hit.actor)}" if hit.actor else ""
        evidence = f" evidence={terminal_text(hit.evidence_ref)}" if hit.evidence_ref else ""
        matches = ",".join(terminal_text(token) for token in hit.matched_tokens)
        lines.append(
            f"- seq={hit.seq} kind={terminal_text(hit.kind)} "
            f"source={terminal_text(hit.source)}{task}{actor} "
            f"score={hit.score:.3f} matches={matches}{evidence}: {terminal_text(hit.text)}"
        )
    return "\n".join(lines)


def _tokens(text: str) -> tuple[str, ...]:
    """Return stable unique, lowercase tokens from ``text`` without stopwords."""
    return tuple(
        sorted({token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS})
    )


def _event_text(event: StoredEvent) -> tuple[str, str]:
    """Return the searchable text and payload source field for ``event``."""
    payload = event.payload
    if event.kind == EventKind.FINDING:
        return _text_field(payload, "statement"), "finding.statement"
    if event.kind == EventKind.CHECKPOINT:
        return _join_fields(payload, ("checkpoint", "note", "paths")), "checkpoint.checkpoint"
    return _join_fields(payload, ("note", "checkpoint", "paths")), "handoff.note"


def _join_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> str:
    """Join text-bearing payload fields in order."""
    parts = [_text_field(payload, field) for field in fields]
    return " ".join(part for part in parts if part)


def _text_field(payload: dict[str, Any], field: str) -> str:
    """Return ``payload[field]`` as searchable text when it is string-like."""
    value = payload.get(field)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(item for item in value if isinstance(item, str))
    return ""


def _actor(payload: dict[str, Any]) -> str:
    """Return the best available actor identity from a memory payload."""
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        actor = provenance.get("actor")
        if isinstance(actor, str) and actor:
            return actor
    for field in ("owner", "actor", "from", "to"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return ""
