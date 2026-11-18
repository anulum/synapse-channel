# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — periodic check, expiry and overlap audit of a metrics token set
"""Watch the metrics token set while the hub serves, not only when a request arrives.

A hub whose metrics bearer follows a token set (``--metrics-token-set``) judges
each metrics or health request against the set in force and reads no file.
:class:`MetricsTokenWatch` follows the rest of the set's life:

* the file is checked every :data:`WATCH_INTERVAL_SECONDS`, the interval of the
  connect-token set, off the serving loop: it is read in a worker thread and a
  change is put in force on the loop in one assignment, so an idle hub applies
  or rejects a replaced file without waiting for a scraper, and no request
  waits for the file;
* each entry that passes its ``not_after`` is recorded once, by whichever comes
  first, the periodic check or a request (``metrics_token_expired``);
* a request admitted by a *retiring* entry is recorded once per entry and hour
  (``metrics_token_retiring_admission``).

An entry E is *retiring* at time t when E is valid at t and another valid entry F
is newer: ``F.not_before`` is later than ``E.not_before``, an open ``not_before``
counting as the earliest, and on equal values the entry later in the file is the
newer. Valid means able to admit a metrics request at t: inside the validity
window, naming no agents, and not refused by the set's token policy
(``require_expiry``, ``max_token_days``).

Both kinds of row are remembered by the running hub process only. A restarted hub
records an entry that is still expired again, and an hour it already recorded for
an entry in an earlier process again. Within one process, the hours recorded per
entry are kept for :data:`RETIRING_MEMORY_HOURS` behind the latest one, so a
validity clock that steps back into a recorded hour writes no second row; an
admission in an hour further back than that writes none, because its use is
already shown by the later rows.

Every row is queued where it arises, also inside a request, and the next check
writes the set's queue in a worker thread
(:meth:`~synapse_channel.core.token_set.TokenSetAuthenticator.flush`), so no
request waits for the journal. A row that cannot be written stays first in the
queue for the next check, and the hub writes the queue once more when it stops
serving. Without a journal the file is still checked periodically; nothing is
recorded.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hourly_once import HOUR_SECONDS, HourlyOnce, hour_of
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import MetricsTokenAudit, metrics_token_audit_for
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS
from synapse_channel.core.token_set import TokenEntry, TokenOutcome, TokenSetAuthenticator

__all__ = [
    "HOUR_SECONDS",
    "RETIRING_MEMORY_HOURS",
    "MetricsTokenWatch",
    "PolicyRefusal",
    "metrics_token_watch_for",
    "newest_valid",
]

RETIRING_MEMORY_HOURS = 24
"""How many hours behind its latest recorded hour an entry's recorded hours are kept."""

_LOG = logging.getLogger(__name__)


PolicyRefusal = Callable[[TokenEntry, float], str | None]
"""A set's token policy: why it refuses an entry at an instant, or ``None``."""


def _valid(entry: TokenEntry, now: float, policy_refusal: PolicyRefusal | None) -> bool:
    """Whether ``entry`` could admit a metrics request at ``now``."""
    return (
        not entry.agents
        and (entry.not_before is None or now >= entry.not_before)
        and (entry.not_after is None or now < entry.not_after)
        and (policy_refusal is None or policy_refusal(entry, now) is None)
    )


def _start(entry: TokenEntry) -> float:
    """Return the entry's ``not_before`` for ordering; an open one is the earliest."""
    return float("-inf") if entry.not_before is None else entry.not_before


def newest_valid(
    entries: tuple[TokenEntry, ...], now: float, policy_refusal: PolicyRefusal | None = None
) -> TokenEntry | None:
    """Return the newest entry valid at ``now``, or ``None`` when none is valid.

    Parameters
    ----------
    entries : tuple of TokenEntry
        The set in force, in file order.
    now : float
        The instant on the validity clock.
    policy_refusal : callable, optional
        The set's token policy, such as
        :meth:`~synapse_channel.core.token_set.TokenSetAuthenticator.policy_refusal`;
        an entry it refuses is not valid. ``None`` applies no policy.

    Returns
    -------
    TokenEntry or None
        The valid entry with the latest ``not_before``; of equal values, the one
        later in the file.
    """
    newest: TokenEntry | None = None
    for entry in entries:
        if _valid(entry, now, policy_refusal) and (
            newest is None or _start(entry) >= _start(newest)
        ):
            newest = entry
    return newest


class MetricsTokenWatch:
    """Check one metrics token set periodically and record its expiries and overlaps.

    Parameters
    ----------
    token_set : TokenSetAuthenticator
        The set the metrics bearer follows. The watch observes the outcome of
        every authentication against it.
    audit : MetricsTokenAudit or None
        Journal writer; ``None`` records nothing.
    """

    def __init__(self, token_set: TokenSetAuthenticator, audit: MetricsTokenAudit | None) -> None:
        self._token_set = token_set
        self._audit = audit
        self._expired: set[str] = set()
        self._retiring = HourlyOnce(RETIRING_MEMORY_HOURS)
        token_set.observe_outcomes(self._observed)

    def check(self) -> None:
        """Check the file, note expiries and write the queue, all on the calling thread.

        This is the synchronous form of :meth:`tick`, for callers that may block.
        """
        self._token_set.refresh()
        self._record_expired("watcher")
        self.flush()

    async def tick(self) -> None:
        """Check the file and write the queue in worker threads; put changes in force here."""
        prepared = await asyncio.to_thread(self._token_set.prepare)
        self._token_set.apply(prepared)
        self._record_expired("watcher")
        await asyncio.to_thread(self.flush)

    async def run(self, interval: float = WATCH_INTERVAL_SECONDS) -> None:
        """Run :meth:`tick` every ``interval`` seconds while the hub serves.

        A failed check is logged and the next one runs; the loop never ends on
        its own, so it cannot stop the hub.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                await self.tick()
            except Exception:
                _LOG.exception("cannot check the metrics token-set file")

    def flush(self) -> None:
        """Write the set's queued rows in order; the hub calls this once more when it stops."""
        self._token_set.flush()

    def _observed(self, outcome: TokenOutcome) -> None:
        """Record expiries seen at a request and an admission by a retiring entry."""
        self._record_expired("request")
        if outcome.entry is not None:
            self._record_retiring(outcome.entry)

    def _record_expired(self, observed: str) -> None:
        """Queue one row for each entry in force that passed its end of validity."""
        audit = self._audit
        if audit is None:
            return
        now = self._token_set.now()
        for entry in self._token_set.entries:
            not_after = entry.not_after
            if not_after is None or now < not_after or entry.token_id in self._expired:
                continue
            self._expired.add(entry.token_id)
            self._token_set.defer(partial(audit.expired, entry.token_id, not_after, observed))

    def _record_retiring(self, entry: TokenEntry) -> None:
        """Queue the first admission per hour by ``entry`` when a newer entry is valid."""
        audit = self._audit
        if audit is None:
            return
        now = self._token_set.now()
        newest = newest_valid(self._token_set.entries, now, self._token_set.policy_refusal)
        if newest is None or newest.token_id == entry.token_id:
            return
        hour = hour_of(now)
        if not self._retiring.is_new(entry.token_id, hour):
            return
        self._retiring.mark(entry.token_id, hour)
        self._token_set.defer(
            partial(audit.retiring_admission, entry.token_id, newest.token_id, hour * HOUR_SECONDS)
        )


def metrics_token_watch_for(
    token_set: TokenAuthenticator | None, journal: EventStore | None
) -> MetricsTokenWatch | None:
    """Return the watch of a metrics token set, attaching its audit when there is a journal.

    Parameters
    ----------
    token_set : TokenAuthenticator or None
        The authenticator the metrics bearer follows; only a
        :class:`~synapse_channel.core.token_set.TokenSetAuthenticator` has a file
        to watch.
    journal : EventStore or None
        The hub's durable event store.

    Returns
    -------
    MetricsTokenWatch or None
        The watch, already observing the set; ``None`` for a hub without a
        metrics token set.
    """
    if not isinstance(token_set, TokenSetAuthenticator):
        return None
    return MetricsTokenWatch(token_set, metrics_token_audit_for(token_set, journal))
