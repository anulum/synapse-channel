# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pair the sender's and the receiver's record of a native message
"""Say which native messages have a record on both sides, and which do not.

A message that travelled over a vendor's own channel is recorded by the seat
on each side. These are caller declarations: even a pair does not independently
prove channel acceptance, arrival or provider processing. This module reads
stored records and sorts them into:

* **paired**: a sender's outcome record and a receiver's record of the same
  message;
* **sender only**: declared sent, with no receiver's record;
* **receiver only**: declared received, with no sender's record;
* **attempts without outcome**: a send that was announced and never reported.

The record schema gives a receiver no phase of its own: its record is valid
as an ``attempt`` or as an ``outcome``. Either one is the receiver's record
here. When a receiver wrote both for one message, identified by its native
message id or else by its sent time, the outcome is kept.

Two records are of the same message when they name the same channel and the
same text digest, and agree on the native session of the sender and of the
recipient wherever both state it. At least one of the two session identifiers
must be stated by both: the same short text sent between other sessions is
not the same message. Each record is paired at most once, in order of time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.terminal_text import terminal_text

Record = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PairingReport:
    """Stored records sorted by what the other side says."""

    paired: tuple[tuple[Record, Record], ...]
    sender_only: tuple[Record, ...]
    receiver_only: tuple[Record, ...]
    attempts_without_outcome: tuple[Record, ...]

    def counts(self) -> dict[str, int]:
        """Return the size of each group."""
        return {
            "paired": len(self.paired),
            "sender_only": len(self.sender_only),
            "receiver_only": len(self.receiver_only),
            "attempts_without_outcome": len(self.attempts_without_outcome),
        }


def _same_message(sent: Record, received: Record) -> bool:
    if sent.get("channel") != received.get("channel"):
        return False
    if sent.get("text_sha256") != received.get("text_sha256"):
        return False
    agreed = 0
    for field in ("sender_native_session", "recipient_native_session"):
        ours, theirs = sent.get(field), received.get(field)
        if ours and theirs:
            if ours != theirs:
                return False
            agreed += 1
    return agreed > 0


def _time(record: Record) -> str:
    return str(record.get("sent_at") or record.get("recorded_at") or "")


def _call(record: Record) -> tuple[object, object, object, object]:
    return (
        record.get("channel"),
        record.get("direction"),
        record.get("sender_native_session"),
        record.get("native_call_id"),
    )


def _receiver_records(stored: list[Record]) -> list[Record]:
    """Return one record per received message, by time; an outcome beats an attempt."""
    kept: dict[tuple[object, ...], Record] = {}
    for record in sorted(stored, key=_time):
        if record.get("direction") != "received":
            continue
        if record.get("phase") not in {"attempt", "outcome"}:
            continue
        message = (
            record.get("channel"),
            record.get("text_sha256"),
            record.get("sender_native_session"),
            record.get("recipient_native_session"),
            record.get("recorder"),
            record.get("native_message_id") or record.get("sent_at"),
        )
        earlier = kept.get(message)
        if earlier is None or (earlier.get("phase"), record.get("phase")) == ("attempt", "outcome"):
            kept[message] = record
    return sorted(kept.values(), key=_time)


def pair_records(records: Iterable[object]) -> PairingReport:
    """Sort stored native-message records by whether the other side recorded too.

    Parameters
    ----------
    records : iterable
        Stored records: the fields of a ``native_message_record`` as the hub
        keeps them. Anything that is not a mapping, or not an ``outcome`` or
        ``attempt`` record of a known direction, is ignored.

    Returns
    -------
    PairingReport
        Every outcome record appears exactly once: in a pair, as sender only
        or as receiver only. A sender's attempt appears only when no outcome
        record of the same call exists. A receiver's record of either phase
        appears once, in a pair or as receiver only.
    """
    stored: list[Record] = [record for record in records if isinstance(record, Mapping)]
    outcomes = [record for record in stored if record.get("phase") == "outcome"]
    sent = sorted((r for r in outcomes if r.get("direction") == "sent"), key=_time)
    received = _receiver_records(stored)
    taken: set[int] = set()
    pairs: list[tuple[Record, Record]] = []
    alone: list[Record] = []
    for ours in sent:
        match = next(
            (
                index
                for index, theirs in enumerate(received)
                if index not in taken and _same_message(ours, theirs)
            ),
            None,
        )
        if match is None:
            alone.append(ours)
        else:
            taken.add(match)
            pairs.append((ours, received[match]))
    reported = {_call(record) for record in outcomes if record.get("native_call_id")}
    attempts = [
        record
        for record in stored
        if record.get("phase") == "attempt"
        and record.get("direction") == "sent"
        and _call(record) not in reported
    ]
    return PairingReport(
        paired=tuple(pairs),
        sender_only=tuple(alone),
        receiver_only=tuple(r for index, r in enumerate(received) if index not in taken),
        attempts_without_outcome=tuple(sorted(attempts, key=_time)),
    )


def render(report: PairingReport) -> list[str]:
    """Return the report as operator-facing lines, safe to print.

    Seats, addresses and channel names are client-chosen text; every line is
    passed through the terminal text filter.
    """

    def line(record: Record) -> str:
        digest = str(record.get("text_sha256") or "")[:12]
        return (
            f"{_time(record)} {record.get('channel')} "
            f"{record.get('sender_seat') or record.get('sender_native_session') or '?'} -> "
            f"{record.get('recipient_seat') or record.get('recipient_address') or '?'} "
            f"sha256 {digest} outcome {record.get('outcome') or '-'}"
        )

    counts = report.counts()
    lines = [
        f"paired: {counts['paired']}, sender only: {counts['sender_only']}, "
        f"receiver only: {counts['receiver_only']}, "
        f"attempts without outcome: {counts['attempts_without_outcome']}"
    ]
    for title, group in (
        ("sender only (no receiver's record)", report.sender_only),
        ("receiver only (no sender's record)", report.receiver_only),
        ("attempts without outcome", report.attempts_without_outcome),
    ):
        if group:
            lines.append(f"{title}:")
            lines.extend(f"  {line(record)}" for record in group)
    return [terminal_text(text) for text in lines]
