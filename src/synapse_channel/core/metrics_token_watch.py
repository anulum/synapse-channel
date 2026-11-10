# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — periodic check, expiry and overlap audit of a metrics token set
"""Watch the metrics token set while the hub serves, not only when a request arrives.

A hub whose metrics bearer follows a token set (``--metrics-token-set``) re-reads
the file at each metrics or health request. :class:`MetricsTokenWatch` adds the
rest of the set's life:

* the file is also checked every :data:`WATCH_INTERVAL_SECONDS`, the interval of
  the connect-token set, so an idle hub applies or rejects a replaced file
  without waiting for a scraper;
* each entry that passes its ``not_after`` is recorded once, by whichever comes
  first, the periodic check or a request (``metrics_token_expired``);
* a request admitted by a *retiring* entry is recorded once per entry and hour
  (``metrics_token_retiring_admission``).

An entry E is *retiring* at time t when E is valid at t and another valid entry F
is newer: ``F.not_before`` is later than ``E.not_before``, an open ``not_before``
counting as the earliest, and on equal values the entry later in the file is the
newer. Valid means inside the validity window and naming no agents, because an
entry that names agents admits no metrics request.

Without a journal the file is still checked periodically; nothing is recorded.
An entry is marked as recorded only after its row is written, so a failed write
is tried again at the next check or admission.
"""

from __future__ import annotations

import asyncio
import logging

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import MetricsTokenAudit, metrics_token_audit_for
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS
from synapse_channel.core.token_set import TokenEntry, TokenOutcome, TokenSetAuthenticator

HOUR_SECONDS = 3600.0
"""Length of one retiring-admission window."""

_LOG = logging.getLogger(__name__)


def _valid(entry: TokenEntry, now: float) -> bool:
    """Whether ``entry`` could admit a metrics request at ``now``."""
    return (
        not entry.agents
        and (entry.not_before is None or now >= entry.not_before)
        and (entry.not_after is None or now < entry.not_after)
    )


def _start(entry: TokenEntry) -> float:
    """Return the entry's ``not_before`` for ordering; an open one is the earliest."""
    return float("-inf") if entry.not_before is None else entry.not_before


def newest_valid(entries: tuple[TokenEntry, ...], now: float) -> TokenEntry | None:
    """Return the newest entry valid at ``now``, or ``None`` when none is valid.

    Parameters
    ----------
    entries : tuple of TokenEntry
        The set in force, in file order.
    now : float
        The instant on the validity clock.

    Returns
    -------
    TokenEntry or None
        The valid entry with the latest ``not_before``; of equal values, the one
        later in the file.
    """
    newest: TokenEntry | None = None
    for entry in entries:
        if _valid(entry, now) and (newest is None or _start(entry) >= _start(newest)):
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
        self._hour: int | None = None
        self._retiring_seen: set[str] = set()
        token_set.observe_outcomes(self._observed)

    def check(self) -> None:
        """Re-read the file if it changed, then record entries that expired."""
        self._token_set.refresh()
        self._record_expired("watcher")

    async def run(self, interval: float = WATCH_INTERVAL_SECONDS) -> None:
        """Call :meth:`check` every ``interval`` seconds while the hub serves.

        A failed check is logged and the next one runs; the loop never ends on
        its own, so it cannot stop the hub.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                self.check()
            except Exception:
                _LOG.exception("cannot check the metrics token-set file")

    def _observed(self, outcome: TokenOutcome) -> None:
        """Record expiries seen at a request and an admission by a retiring entry."""
        self._record_expired("request")
        if outcome.entry is not None:
            self._record_retiring(outcome.entry)

    def _record_expired(self, observed: str) -> None:
        """Write one row for each entry in force that passed its end of validity."""
        if self._audit is None:
            return
        now = self._token_set.now()
        for entry in self._token_set.entries:
            not_after = entry.not_after
            if not_after is None or now < not_after or entry.token_id in self._expired:
                continue
            self._audit.expired(entry.token_id, not_after, observed)
            self._expired.add(entry.token_id)

    def _record_retiring(self, entry: TokenEntry) -> None:
        """Write the first admission per hour by ``entry`` when a newer entry is valid."""
        if self._audit is None:
            return
        now = self._token_set.now()
        newest = newest_valid(self._token_set.entries, now)
        if newest is None or newest.token_id == entry.token_id:
            return
        hour = int(now // HOUR_SECONDS)
        if hour != self._hour:
            self._hour = hour
            self._retiring_seen.clear()
        if entry.token_id in self._retiring_seen:
            return
        self._audit.retiring_admission(entry.token_id, newest.token_id, hour * HOUR_SECONDS)
        self._retiring_seen.add(entry.token_id)


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
