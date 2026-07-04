# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — track directed chats that reached no live connection
"""Track directed chats the hub delivered to no live connection.

A message addressed to a name with no live socket is still durable — the
journal and relay mirror keep it — but it wakes no one, and if nobody ever
drains that name's inbox the human ends up relaying it by hand: the exact
failure the bus exists to remove. The hub is the one component that *sees*
this happen at send time, so it keeps a small, bounded ledger of those
targets and serves it in the state snapshot, where the dashboard and the
cockpit can show "N messages, nobody listening" instead of leaving the
blackhole invisible.

Honest scope: an entry means "at send time, no live connection matched the
target". It does not know about inbox cursors on other machines, and it
clears when the exact name connects — arrival is the hub-visible signal
that a reader exists; whether that reader also drains the feed history is
the doctor's addressee check, not this ledger.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_DEAD_LETTER_TARGETS = 200
"""Bounded number of distinct targets retained; the stalest entry is evicted."""

DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
"""Recommended age after which a target with no fresh dead letter is forgotten.

A blackhole nobody has even *tried* to reach in a week is stale — the sender
stopped, so it is noise, not a live problem. A target that keeps drawing
directed traffic nobody reads refreshes ``last_ts`` on every message and so
never ages out; only a target that has gone quiet expires. The hub applies this
default; the ledger itself defaults to no age bound so a library caller keeps
the pure capacity-bounded behaviour."""


def is_directed_target(target: str) -> bool:
    """Return whether ``target`` names one recipient rather than an audience.

    Broadcasts (``all``), group globs (``project/*``), and blank targets are
    audiences — nobody in particular is expected to read them, so missing
    every live connection is not a dead letter.
    """
    name = target.strip()
    return bool(name) and name != "all" and "*" not in name


@dataclass(frozen=True)
class DeadLetter:
    """One target with directed traffic that reached no live connection.

    Attributes
    ----------
    target : str
        The addressed name nobody was connected to receive.
    count : int
        Directed messages that found no live connection, since hub start
        or the target's last connection.
    last_ts : float
        Timestamp of the most recent such message.
    last_sender : str
        Who sent it — the counterpart an operator would answer.
    """

    target: str
    count: int
    last_ts: float
    last_sender: str


class DeadLetterLedger:
    """Bounded per-target ledger of directed chats that reached nobody.

    Parameters
    ----------
    max_targets : int
        Distinct targets retained (floored at ``1``); recording a new
        target beyond the bound evicts the entry with the oldest
        ``last_ts``, so a flood of one-off names cannot grow the hub.
    max_age_seconds : float or None, optional
        When set, a target is forgotten once its ``last_ts`` falls more than
        this many seconds behind the current time, so a blackhole that has
        gone quiet ages out instead of lingering as a stale slot. Expiry runs
        on every :meth:`record` (against the message's own timestamp) and on
        every :meth:`snapshot` (against ``now``), so a read is fresh even
        during a quiet stretch. ``None`` (the default) keeps the pure
        capacity-bounded behaviour with no age bound.
    """

    def __init__(
        self,
        max_targets: int = DEFAULT_DEAD_LETTER_TARGETS,
        *,
        max_age_seconds: float | None = None,
    ) -> None:
        self.max_targets = max(1, int(max_targets))
        self.max_age_seconds = None if max_age_seconds is None else float(max_age_seconds)
        self._entries: dict[str, DeadLetter] = {}

    def _expire_stale(self, now: float) -> None:
        """Drop targets whose most recent dead letter is older than the age bound."""
        if self.max_age_seconds is None:
            return
        cutoff = now - self.max_age_seconds
        for target in [t for t, entry in self._entries.items() if entry.last_ts < cutoff]:
            del self._entries[target]

    def record(self, target: str, *, sender: str, ts: float) -> None:
        """Count one directed message that matched no live connection."""
        known = self._entries.get(target)
        count = known.count + 1 if known is not None else 1
        self._entries[target] = DeadLetter(
            target=target, count=count, last_ts=float(ts), last_sender=sender
        )
        self._expire_stale(float(ts))
        if len(self._entries) > self.max_targets:
            stalest = min(self._entries.values(), key=lambda entry: entry.last_ts)
            del self._entries[stalest.target]

    def clear(self, name: str) -> None:
        """Forget a target — its reader just connected."""
        self._entries.pop(name, None)

    def snapshot(self, now: float | None = None) -> list[dict[str, object]]:
        """Return the ledger for the state snapshot, worst first.

        Aged-out targets are expired against ``now`` (wall clock when ``None``)
        before the view is built, mirroring the state snapshot's own expiry, so
        the ledger never reports a blackhole that has gone quiet past the age
        bound. Sorted by count descending, then target, so the biggest
        blackhole leads; the shape is one JSON object per target.
        """
        self._expire_stale(time.time() if now is None else float(now))
        ordered = sorted(self._entries.values(), key=lambda entry: (-entry.count, entry.target))
        return [
            {
                "target": entry.target,
                "count": entry.count,
                "last_ts": entry.last_ts,
                "last_sender": entry.last_sender,
            }
            for entry in ordered
        ]
