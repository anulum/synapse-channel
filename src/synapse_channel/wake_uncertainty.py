# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bring uncertain wakes to an operational end
"""List uncertain wakes until an operator resolves them, and ask when they age.

A wake whose outcome is unknown is never repeated (see
:mod:`synapse_channel.wake_relay`). Left alone, such wakes would pile up
unseen. This module reads the durable marks and reports, for each uncertain
wake that no operator has resolved:

* how old it is, and what it holds up: the wake of that one message;
* whether repeating the transport is safe or could make a second effect;
* that no automatic verifier exists, and what the operator may do;
* the condition under which work continues safely;
* which later wakes of the same seat ran while it was unresolved. Each such
  wake was a fresh request to the same seat, so it may have been a second
  effect. It is detected and reported, not hidden;
* once it is older than a threshold, an explicit operator question that
  names the marks directory as its evidence.

Nothing here runs an adapter, writes a mark or changes what the relay does.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from synapse_channel import wake_marks
from synapse_channel.wake_adapters import UNCERTAIN

REPEAT_SAFE_KINDS = frozenset({"harness_waiter"})
"""Transports whose repeat only writes another passive notice."""

DEFAULT_QUESTION_AFTER_SECONDS = 900.0
"""Age at which an unresolved uncertain wake becomes an operator question."""

OPERATOR_ACTIONS = (
    "inspect the seat's session for the wake prompt",
    "synapse waker resolve --as read, when the seat read the message",
    "synapse waker resolve --as abandoned, when the wake is given up",
)
"""What an operator may do. Re-running the wake is not among them."""

SAFE_CONTINUATION = (
    "an operator resolution is recorded; the message stays in the mailbox and the "
    "wake is not run again while its marks are kept (pruning may later drop resolved marks)"
)
"""When work on this wake may continue safely."""


@dataclass(frozen=True, slots=True)
class UncertainWake:
    """One unresolved uncertain wake and what is known about it."""

    seat: str
    wake_id: int
    adapter: str
    state: str
    since: str
    age_seconds: float | None
    repeat: str
    verifier: str
    operator_actions: tuple[str, ...]
    safe_continuation: str
    followed_by: tuple[int, ...]
    question: str
    evidence: str

    def to_json(self) -> dict[str, object]:
        """Return a JSON-ready mapping with list fields as lists."""
        data = asdict(self)
        data["operator_actions"] = list(self.operator_actions)
        data["followed_by"] = list(self.followed_by)
        return data


def _parse(stamp: str) -> datetime | None:
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc)


def _row(
    directory: Path,
    seat: str,
    wake_id: int,
    later: tuple[int, ...],
    *,
    now: datetime,
    question_after_seconds: float,
) -> UncertainWake:
    """Describe the uncertain attempt of one wake."""
    marks = wake_marks.read(directory, seat, wake_id)
    mark = next(mark for mark in marks if mark.state in {wake_marks.OPEN, UNCERTAIN})
    since = mark.at or wake_marks.attempt_time(directory, seat, wake_id, mark)
    moment = _parse(since)
    age = None if moment is None else max((now - moment).total_seconds(), 0.0)
    evidence = str(directory / wake_marks.seat_key(seat) / str(wake_id))
    question = ""
    if age is None or age >= question_after_seconds:
        question = (
            f"Did {seat} read message {wake_id}? The {mark.adapter} wake is uncertain"
            f"{'' if age is None else f' since {since}'}; evidence: {evidence}"
        )
    return UncertainWake(
        seat=seat,
        wake_id=wake_id,
        adapter=mark.adapter,
        state="interrupted" if mark.state == wake_marks.OPEN else UNCERTAIN,
        since=since,
        age_seconds=age,
        repeat="safe" if mark.adapter in REPEAT_SAFE_KINDS else "second_effect",
        verifier="none automatic; only an operator can inspect the seat's session",
        operator_actions=OPERATOR_ACTIONS,
        safe_continuation=SAFE_CONTINUATION,
        followed_by=later,
        question=question,
        evidence=evidence,
    )


def unresolved(
    directory: Path,
    *,
    now: datetime,
    question_after_seconds: float = DEFAULT_QUESTION_AFTER_SECONDS,
) -> tuple[UncertainWake, ...]:
    """Return every uncertain wake that no operator has resolved, oldest message first.

    Parameters
    ----------
    directory : pathlib.Path
        Directory of the durable marks.
    now : datetime
        Current UTC time, used for ages.
    question_after_seconds : float, optional
        Age from which a wake carries an operator question. A wake whose start
        time cannot be read always carries one.

    Returns
    -------
    tuple[UncertainWake, ...]
        Unresolved uncertain wakes, by seat and then by wake id.

    Raises
    ------
    ValueError
        If the threshold is negative or not finite.
    """
    if not 0.0 <= question_after_seconds < float("inf"):
        raise ValueError("the question threshold is a finite, non-negative number of seconds")
    rows: list[UncertainWake] = []
    for seat in wake_marks.seats(directory):
        ids = wake_marks.wake_ids(directory, seat)
        for wake_id in ids:
            if not wake_marks.is_uncertain(wake_marks.read(directory, seat, wake_id)):
                continue
            if wake_marks.resolution(directory, seat, wake_id) is not None:
                continue
            later = tuple(other for other in ids if other > wake_id)
            rows.append(
                _row(
                    directory,
                    seat,
                    wake_id,
                    later,
                    now=now,
                    question_after_seconds=question_after_seconds,
                )
            )
    return tuple(rows)
