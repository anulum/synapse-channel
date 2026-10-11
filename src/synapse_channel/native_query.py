# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — query the stored records of native vendor messages
"""Read stored native-message records and say which have both sides.

This is the query ``native-messages <seat|all>`` of ``synapse event-query``.
It reads the ``native_message`` events of an event store, pairs the sender's
and the receiver's record of each message, and lists the records that have
no counterpart.

All stored records are paired first and the selector is applied afterwards,
to whole pairs. A sender's record may name its recipient only by a raw
address; selecting before pairing would then show the receiver's record as
one-sided although the sender recorded too.

The message text and the tool result are never printed. A record names its
text by digest and size; the text itself stays in the store.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.native_pairing import PairingReport, Record, pair_records, render
from synapse_channel.terminal_text import terminal_text

QUERY_WORD = "native-messages"
"""First word of the query."""

ALL = "all"
"""Selector that keeps every record."""

RESULT_KIND = "native_messages"
"""``kind`` of the JSON result."""

# The recorder is always one of the two: the hub accepts a record only from
# the seat on its own side.
_SEAT_FIELDS = ("sender_seat", "recipient_seat")
_WITHHELD = frozenset({"text", "tool_result"})


@dataclass(frozen=True, slots=True)
class NativeMessageResult:
    """The records of one selector, sorted by what the other side says."""

    query: str
    selector: str
    report: PairingReport


def selector_of(query: str) -> str | None:
    """Return the selector of a ``native-messages <seat|all>`` query.

    Returns
    -------
    str or None
        The seat or ``all``; None when ``query`` is another kind of query.
    """
    tokens = query.split()
    if len(tokens) == 2 and tokens[0] == QUERY_WORD:
        return tokens[1]
    return None


def _selected(record: Record, selector: str) -> bool:
    return selector == ALL or selector in {record.get(field) for field in _SEAT_FIELDS}


def select(report: PairingReport, selector: str) -> PairingReport:
    """Keep what concerns ``selector``: a pair when either of its records does."""
    return PairingReport(
        paired=tuple(
            pair for pair in report.paired if any(_selected(item, selector) for item in pair)
        ),
        sender_only=tuple(r for r in report.sender_only if _selected(r, selector)),
        receiver_only=tuple(r for r in report.receiver_only if _selected(r, selector)),
        attempts_without_outcome=tuple(
            r for r in report.attempts_without_outcome if _selected(r, selector)
        ),
    )


def run_native_query(
    db_path: str | Path, query: str, *, key_file: str | Path | None = None
) -> NativeMessageResult:
    """Pair the stored native-message records of an event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event store.
    query : str
        ``native-messages <seat|all>``.
    key_file : str or pathlib.Path or None
        Owner-only SQLCipher key when the store is encrypted.

    Returns
    -------
    NativeMessageResult
        Pairs and one-sided records that concern the selector. Each record
        carries the sequence number of its event as ``event_seq``.

    Raises
    ------
    ValueError
        The store does not exist, or ``query`` is not a native-message query.
    """
    selector = selector_of(query)
    if selector is None:
        raise ValueError(f"unsupported event query: {query}")
    path = Path(db_path)
    if not path.exists():
        raise ValueError(f"missing event store: {path}")
    store = EventStore(path, key_file=key_file)
    try:
        events = store.read_window(kinds=(EventKind.NATIVE_MESSAGE,))
    finally:
        store.close()
    records = [{**event.payload, "event_seq": event.seq} for event in events]
    return NativeMessageResult(query, selector, select(pair_records(records), selector))


def _newest(records: Sequence[Any], limit: int | None) -> tuple[Any, ...]:
    if limit is None:
        return tuple(records)
    return tuple(records[-limit:]) if limit > 0 else ()


def capped(result: NativeMessageResult, limit: int | None) -> NativeMessageResult:
    """Keep the newest ``limit`` entries of each group; None keeps all."""
    report = result.report
    return NativeMessageResult(
        result.query,
        result.selector,
        PairingReport(
            paired=_newest(report.paired, limit),
            sender_only=_newest(report.sender_only, limit),
            receiver_only=_newest(report.receiver_only, limit),
            attempts_without_outcome=_newest(report.attempts_without_outcome, limit),
        ),
    )


def _public(record: Record) -> dict[str, Any]:
    return {name: value for name, value in record.items() if name not in _WITHHELD}


def result_to_json(result: NativeMessageResult, *, limit: int | None = None) -> dict[str, object]:
    """Return the result as plain data; counts are of the whole result.

    ``limit`` cuts the listed entries of each group to the newest ones. The
    message text and the tool result are left out of every record.
    """
    shown = capped(result, limit).report
    return {
        "kind": RESULT_KIND,
        "query": result.query,
        "selector": result.selector,
        "counts": result.report.counts(),
        "paired": [
            {"sent": _public(sent), "received": _public(received)}
            for sent, received in shown.paired
        ],
        "sender_only": [_public(record) for record in shown.sender_only],
        "receiver_only": [_public(record) for record in shown.receiver_only],
        "attempts_without_outcome": [_public(record) for record in shown.attempts_without_outcome],
    }


def render_human(result: NativeMessageResult, *, limit: int | None = None) -> str:
    """Return the result as terminal text; every line is made safe to print.

    The first line counts the whole result; ``limit`` cuts only the listed
    one-sided records to the newest ones.
    """
    counts = result.report.counts()
    listed = render(capped(result, limit).report)[1:]
    head = (
        f"native messages {result.selector}: paired {counts['paired']}, "
        f"sender only {counts['sender_only']}, receiver only {counts['receiver_only']}, "
        f"attempts without outcome {counts['attempts_without_outcome']}"
    )
    return "\n".join(terminal_text(line) for line in (head, *listed))
