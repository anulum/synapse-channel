# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one declared operation's chain, read from a hub event store
"""Report one declared operation's chain from a hub event store, read only.

The report reads the rows the hub journals for an operation: its declaration
with the authorisation intent, the approval notes on the intent's digest, the
hub's answers to each request for an attempt, the executor's outcome reports,
the independent verifications and the seals. It folds them with the hub's own
readers, :class:`~synapse_channel.core.operation_attempt_ledger.AttemptLedger`
and :func:`~synapse_channel.core.approvals.build_approval_report`, so it never
gives a state the hub would not.

Every link keeps its journal sequence and time. A missing row is "not
recorded", never "did not happen": an executor's report is never a success
without an independent verification, and an open attempt is never ended by its
deadline. The operation's state is that of its most consequential attempt, in
:data:`ATTEMPT_PRECEDENCE`; an operation with no attempt takes the latest of its
refusal and its approval decision. Custody is not a record this hub keeps.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.approvals import AWAITING, ApprovalStatus, build_approval_report
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import (
    STATE_CLOSED,
    STATE_ENDED,
    STATE_OPEN,
    STATE_RECONCILED,
    STATE_SEALED,
    TERMINAL_OUTCOME_STATES,
    AttemptLedger,
    AttemptSummary,
)
from synapse_channel.core.operation_attempts import APPROVED_FRESH
from synapse_channel.core.persistence import EventStore, StoredEvent

NOT_RECORDED: Final = "not recorded"
VERIFIED: Final = "verified"
SEALED: Final = "sealed"
ENDED_UNRECONCILED: Final = "ended, unreconciled"
OUTCOME_UNKNOWN: Final = "outcome reported, unknown"
OPEN_SILENT: Final = "attempt open, silent"
OPEN: Final = "attempt open"
RECONCILED: Final = "reconciled"
DECLARED: Final = "declared"
CUSTODY: Final = "not recorded here"

ATTEMPT_PRECEDENCE: Final = (
    VERIFIED,
    SEALED,
    ENDED_UNRECONCILED,
    OUTCOME_UNKNOWN,
    OPEN_SILENT,
    OPEN,
    RECONCILED,
)
"""The attempt states, most consequential first; the operation takes the first one it has."""

_KINDS: Final = {
    EventKind.OPERATION_DECLARED: "declared",
    EventKind.OPERATION_ATTEMPT_ANSWERED: "answer",
    EventKind.OPERATION_OUTCOME: "outcome",
    EventKind.OPERATION_VERIFIED: "verification",
    EventKind.OPERATION_SEALED: "seal",
}
_DETAIL: Final = {
    "declared": ("effect", "intent_sha256"),
    "answer": ("answer", "attempt_id", "request_key"),
    "outcome": ("state", "boundary", "external_effect"),
    "verification": ("effect", "result"),
    "seal": ("attempt_id",),
}


@dataclass(frozen=True)
class ChainLink:
    """One journalled row of the operation, as evidence.

    Attributes
    ----------
    seq, at : int, float
        The row's journal sequence and the hub's time.
    kind : str
        ``declared``, ``approval``, ``answer``, ``outcome``, ``verification`` or ``seal``.
    actor : str
        The row's sender, or the approval note's author.
    detail : Mapping[str, object]
        The row's fields that name what it says, and nothing else.
    """

    seq: int
    at: float
    kind: str
    actor: str
    detail: Mapping[str, object]


@dataclass(frozen=True)
class AttemptReport:
    """One attempt: its id, its executor and its state in :data:`ATTEMPT_PRECEDENCE`."""

    attempt_id: str
    executor: str
    state: str


@dataclass(frozen=True)
class OperationReport:
    """One operation's state and chain, as of the store's last row.

    Attributes
    ----------
    operation_id, task_id : str
        The operation and its task; ``task_id`` is empty when nothing is recorded.
    state : str
        One of :data:`ATTEMPT_PRECEDENCE`, ``refused: <answer>``, ``awaiting approval``,
        ``approved, no attempt``, ``approval rejected``, ``declared`` or ``not recorded``.
    intent_sha256 : str
        The digest the declaration kept; empty for a declaration without an intent.
    digest_check : str
        ``matches`` when the kept intent's canonical digest equals the kept digest,
        ``differs`` when it does not, ``no intent`` when none was kept.
    attempts, chain : tuple
        The attempts in opening order; every link in journal order.
    as_of_seq, as_of : int, float
        The store's last row: its sequence and the hub's time, the clock a silent
        attempt is judged by.
    custody : str
        Always :data:`CUSTODY`: custody is not a record this hub keeps.
    recomputed_sha256 : str
        The kept intent's digest as this reader recomputes it from the intent's
        canonical bytes; empty when no intent was kept.
    intent_fields : tuple
        The kept intent's fields in canonical order, each with its value's
        canonical JSON text; empty when no intent was kept.
    """

    operation_id: str
    task_id: str
    state: str
    intent_sha256: str
    digest_check: str
    attempts: tuple[AttemptReport, ...]
    chain: tuple[ChainLink, ...]
    as_of_seq: int
    as_of: float
    custody: str = CUSTODY
    recomputed_sha256: str = ""
    intent_fields: tuple[tuple[str, str], ...] = ()

    @property
    def declaration(self) -> ChainLink | None:
        """Return the declaration's link, or ``None`` when nothing is declared.

        The approval notes on the intent's digest join the chain in journal order,
        and a note can be journalled before the declaration, so the first link is
        not always the declaration.
        """
        declared = _KINDS[EventKind.OPERATION_DECLARED]
        return next((link for link in self.chain if link.kind == declared), None)


def _read_events(db_path: str | Path, key_file: str | Path | None) -> tuple[StoredEvent, ...]:
    """Read every row of the hub event store at ``db_path``; refuse a store that does not exist."""
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        return tuple(store.read_all())
    finally:
        store.close()


def run_operation_report(
    db_path: str | Path, operation_id: str, *, key_file: str | Path | None = None
) -> OperationReport:
    """Read ``operation_id``'s chain from the hub event store at ``db_path``.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return build_operation_report(operation_id, _read_events(db_path, key_file))


def run_operation_reports(
    db_path: str | Path, *, key_file: str | Path | None = None
) -> tuple[OperationReport, ...]:
    """Read the report of every declared operation in the hub event store at ``db_path``.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return list_operation_reports(_read_events(db_path, key_file))


def build_operation_report(operation_id: str, events: Sequence[StoredEvent]) -> OperationReport:
    """Fold ``operation_id``'s rows in ``events`` into its report."""
    rows = [event for event in events if _is_of(event, operation_id)]
    approvals = build_approval_report(events).by_subject
    return _report(operation_id, rows, approvals, _as_of(events))


def list_operation_reports(events: Sequence[StoredEvent]) -> tuple[OperationReport, ...]:
    """Return the report of every declared operation in ``events``, in declaration order.

    The approval notes are read once for all operations, so a store of many
    operations is read in one pass.
    """
    rows_by_operation: dict[str, list[StoredEvent]] = {}
    for event in events:
        operation_id = event.payload.get("operation_id") if event.kind in _KINDS else None
        if isinstance(operation_id, str):
            rows_by_operation.setdefault(operation_id, []).append(event)
    declared = sorted(
        (
            (next(row.seq for row in rows if row.kind == EventKind.OPERATION_DECLARED), key)
            for key, rows in rows_by_operation.items()
            if any(row.kind == EventKind.OPERATION_DECLARED for row in rows)
        )
    )
    approvals = build_approval_report(events).by_subject
    as_of = _as_of(events)
    return tuple(_report(key, rows_by_operation[key], approvals, as_of) for _seq, key in declared)


def _as_of(events: Sequence[StoredEvent]) -> tuple[int, float]:
    """Return the store's last sequence and the hub's last recorded time."""
    return (
        max((event.seq for event in events), default=0),
        max((event.ts for event in events), default=0.0),
    )


def _report(
    operation_id: str,
    rows: Sequence[StoredEvent],
    approvals: Mapping[str, ApprovalStatus],
    as_of: tuple[int, float],
) -> OperationReport:
    """Fold one operation's rows into its report, given the store's approvals and last row."""
    as_of_seq, as_of_time = as_of
    declaration = next((row for row in rows if row.kind == EventKind.OPERATION_DECLARED), None)
    if declaration is None:
        return OperationReport(
            operation_id, "", NOT_RECORDED, "", "no intent", (), (), as_of_seq, as_of_time
        )
    ledger = AttemptLedger(None)
    for row in rows:
        ledger.apply_event(row)
    digest = str(declaration.payload.get("intent_sha256") or "")
    approval = approvals.get(f"sha256:{digest}") if digest else None
    attempts = _attempts(operation_id, ledger, rows, _deadline(declaration), as_of_time)
    intent = declaration.payload.get("intent")
    recomputed = _recomputed(intent)
    return OperationReport(
        operation_id=operation_id,
        task_id=str(declaration.payload.get("task_id") or ""),
        state=_state(attempts, rows, approval),
        intent_sha256=digest,
        digest_check=_digest_check(recomputed, digest),
        attempts=attempts,
        chain=_chain(rows, approval),
        as_of_seq=as_of_seq,
        as_of=as_of_time,
        recomputed_sha256=recomputed,
        intent_fields=_intent_fields(intent),
    )


def _is_of(event: StoredEvent, operation_id: str) -> bool:
    return event.kind in _KINDS and event.payload.get("operation_id") == operation_id


def _deadline(declaration: StoredEvent) -> int | None:
    intent = declaration.payload.get("intent")
    deadline = intent.get("deadline") if isinstance(intent, Mapping) else None
    return deadline if isinstance(deadline, int) and not isinstance(deadline, bool) else None


def _attempts(
    operation_id: str,
    ledger: AttemptLedger,
    rows: Sequence[StoredEvent],
    deadline: int | None,
    as_of: float,
) -> tuple[AttemptReport, ...]:
    """Return each attempt with the hub's state, refined where the rows say more."""
    summary = ledger.summary(operation_id)
    if summary is None:
        return ()
    _closed, attempts = summary
    return tuple(
        AttemptReport(item.attempt_id, item.executor, _attempt_state(item, rows, deadline, as_of))
        for item in attempts
    )


def _attempt_state(
    item: AttemptSummary, rows: Sequence[StoredEvent], deadline: int | None, as_of: float
) -> str:
    """Map the ledger's attempt state to the report's words."""
    ledger_state = item.state
    if ledger_state == STATE_CLOSED:
        return VERIFIED
    if ledger_state == STATE_RECONCILED:
        return RECONCILED
    if ledger_state == STATE_SEALED:
        return SEALED
    if ledger_state == STATE_ENDED:
        return ENDED_UNRECONCILED
    if ledger_state == STATE_OPEN and _reported_unfinished(item, rows):
        return OUTCOME_UNKNOWN
    if deadline is not None and as_of >= deadline:
        return OPEN_SILENT
    return OPEN


def _reported_unfinished(item: AttemptSummary, rows: Sequence[StoredEvent]) -> bool:
    """Whether the attempt's own executor reported a state that does not end it, after it opened."""
    opened = next(
        (
            row.seq
            for row in rows
            if row.kind == EventKind.OPERATION_ATTEMPT_ANSWERED
            and row.payload.get("attempt_id") == item.attempt_id
        ),
        None,
    )
    return opened is not None and any(
        row.kind == EventKind.OPERATION_OUTCOME
        and row.seq > opened
        and row.payload.get("sender") == item.executor
        and str(row.payload.get("state") or "") not in TERMINAL_OUTCOME_STATES
        for row in rows
    )


def _state(
    attempts: Sequence[AttemptReport],
    rows: Sequence[StoredEvent],
    approval: ApprovalStatus | None,
) -> str:
    """Return the operation's state: its most consequential attempt, else its latest word."""
    held = {attempt.state for attempt in attempts}
    for state in ATTEMPT_PRECEDENCE:
        if state in held:
            return state
    refusal = next(
        (
            row
            for row in reversed(rows)
            if row.kind == EventKind.OPERATION_ATTEMPT_ANSWERED
            and row.payload.get("answer") != APPROVED_FRESH
        ),
        None,
    )
    decided_seq = approval.history[-1].seq if approval is not None and approval.history else -1
    if refusal is not None and refusal.seq > decided_seq:
        return f"refused: {refusal.payload.get('answer')}"
    if approval is None:
        return DECLARED
    if approval.current_state == AWAITING:
        return "awaiting approval"
    return "approved, no attempt" if approval.current_state == "approved" else "approval rejected"


def _recomputed(intent: object) -> str:
    """Return the kept intent's digest over its canonical bytes, or empty without an intent."""
    if not isinstance(intent, Mapping):
        return ""
    return hashlib.sha256(canonical_json(dict(intent))).hexdigest()


def _digest_check(recomputed: str, digest: str) -> str:
    if not recomputed or not digest:
        return "no intent"
    return "matches" if recomputed == digest else "differs"


def _intent_fields(intent: object) -> tuple[tuple[str, str], ...]:
    """Return the kept intent's fields in canonical order, each value as canonical JSON text.

    The order and the text are those of the bytes the digest covers, so a reader
    shows the document the digest names without encoding it again.
    """
    if not isinstance(intent, Mapping):
        return ()
    ordered = json.loads(canonical_json(dict(intent)))
    return tuple((key, canonical_json(value).decode("utf-8")) for key, value in ordered.items())


def _chain(rows: Sequence[StoredEvent], approval: ApprovalStatus | None) -> tuple[ChainLink, ...]:
    """Return every row of the operation and every approval note on its digest, in journal order."""
    links = [
        ChainLink(
            row.seq,
            row.ts,
            _KINDS[row.kind],
            str(row.payload.get("sender") or ""),
            {key: row.payload[key] for key in _DETAIL[_KINDS[row.kind]] if key in row.payload},
        )
        for row in rows
    ]
    for item in approval.history if approval is not None else ():
        detail = {"state": item.state, "reason": item.reason}
        links.append(ChainLink(item.seq, item.ts, "approval", item.actor, detail))
    return tuple(sorted(links, key=lambda link: link.seq))


def operation_report_to_json(report: OperationReport) -> dict[str, object]:
    """Return a stable JSON-compatible document of ``report``."""
    return {
        "operation_id": report.operation_id,
        "task_id": report.task_id,
        "state": report.state,
        "intent_sha256": report.intent_sha256,
        "digest_check": report.digest_check,
        "attempts": [
            {"attempt_id": item.attempt_id, "executor": item.executor, "state": item.state}
            for item in report.attempts
        ],
        "chain": [
            {
                "seq": link.seq,
                "at": link.at,
                "kind": link.kind,
                "actor": link.actor,
                "detail": dict(link.detail),
            }
            for link in report.chain
        ],
        "as_of_seq": report.as_of_seq,
        "as_of": report.as_of,
        "custody": report.custody,
        "recomputed_sha256": report.recomputed_sha256,
        "intent_fields": [{"field": key, "canonical": text} for key, text in report.intent_fields],
    }


def render_lines(report: OperationReport) -> tuple[str, ...]:
    """Render ``report`` as one line per fact; the caller escapes each line for its terminal.

    A value from the journal stays inside its own line, so a newline in a reason
    cannot forge a line of its own once the caller escapes the line.
    """
    lines = [
        f"operation {report.operation_id}: {report.state}",
        f"task: {report.task_id or '-'}; as of journal seq {report.as_of_seq}",
        f"intent: {report.intent_sha256 or '-'} ({report.digest_check})",
    ]
    if report.digest_check == "differs":
        lines.append(f"intent recomputed: {report.recomputed_sha256}")
    lines += [f"  intent.{key} = {text}" for key, text in report.intent_fields]
    lines += [
        f"attempt {item.attempt_id} by {item.executor}: {item.state}" for item in report.attempts
    ]
    for link in report.chain:
        detail = ", ".join(f"{key}={value}" for key, value in link.detail.items())
        lines.append(f"  #{link.seq} {link.kind} by {link.actor or '-'}: {detail}")
    lines.append(f"custody: {report.custody}")
    return tuple(lines)
