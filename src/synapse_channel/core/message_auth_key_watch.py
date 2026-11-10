# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — periodic check and journal rows of a per-message key set
"""Check a per-message key set periodically and journal its life.

A hub that verifies signed frames against a key set
(:class:`~synapse_channel.core.message_auth_key_set.MessageAuthKeySet`) checks
its files every :data:`WATCH_INTERVAL_SECONDS` as well as at each verification,
and, with a journal, writes four audit-only kinds. They hold key ids and names,
never a secret, a digest or a signature:

``message_auth_key_set_applied``
    The key-set file now in force, with its key ids.
``message_auth_key_set_rejected``
    A set or secret file that was not applied and why; the last set stays.
``message_auth_key_expired``
    A key passed its ``not_after``: its id, that end and whether the periodic
    check or a verification saw it first (``observed`` ``watcher`` or
    ``verification``). One row per key id while one hub process runs.
``message_auth_key_retiring_admission``
    A frame from a sender was verified with a *retiring* key, one that a newer
    key valid for the same sender is meant to replace: the key id, the sender,
    the newest key id for that sender and the start of the hour. One row per
    key, sender and hour while one hub process runs; the hours recorded are
    kept for :data:`RETIRING_MEMORY_HOURS` behind the latest one.

A key K is retiring for sender S when another key F is valid now, names S among
its senders and is newer than K: ``F.not_before`` is later, an open
``not_before`` counting as the earliest, and on equal values the key later in
the file is the newer. Keys are bound to senders, so a newer key for another
sender replaces nothing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path

from synapse_channel.core.hourly_once import HOUR_SECONDS, HourlyOnce, hour_of
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.message_auth_key_set import MessageAuthKeyEntry, MessageAuthKeySet
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.token_audit import AGENT_FIELD_LIMIT
from synapse_channel.core.token_sessions import WATCH_INTERVAL_SECONDS

MESSAGE_KEY_SET_APPLIED_KIND = "message_auth_key_set_applied"
MESSAGE_KEY_SET_REJECTED_KIND = "message_auth_key_set_rejected"
MESSAGE_KEY_EXPIRED_KIND = "message_auth_key_expired"
MESSAGE_KEY_RETIRING_ADMISSION_KIND = "message_auth_key_retiring_admission"

RETIRING_MEMORY_HOURS = 24
"""How many hours behind its latest recorded hour a key and sender's hours are kept."""

_LOG = logging.getLogger(__name__)


class MessageAuthKeyAudit:
    """Write the rows of a per-message key set to a hub journal.

    Parameters
    ----------
    journal : EventStore
        The hub's durable event store.
    """

    def __init__(self, journal: EventStore) -> None:
        self._journal = journal

    def set_applied(self, path: Path, key_ids: tuple[str, ...]) -> None:
        """Record that the key set at ``path`` is in force."""
        self._journal.append(
            MESSAGE_KEY_SET_APPLIED_KIND,
            {"path": str(path), "key_ids": list(key_ids)},
            durable=True,
        )

    def set_rejected(self, path: Path, reason: str) -> None:
        """Record that the key set at ``path`` was not applied."""
        self._journal.append(
            MESSAGE_KEY_SET_REJECTED_KIND, {"path": str(path), "reason": reason}, durable=True
        )

    def expired(self, key_id: str, not_after: float, observed: str) -> None:
        """Record that ``key_id`` passed its ``not_after``.

        Parameters
        ----------
        key_id : str
            The key that expired.
        not_after : float
            Its end of validity, in POSIX seconds.
        observed : str
            ``watcher`` or ``verification``: which check saw the expiry first.
        """
        self._journal.append(
            MESSAGE_KEY_EXPIRED_KIND,
            {"key_id": key_id, "not_after": not_after, "observed": observed},
            durable=True,
        )

    def retiring_admission(
        self, key_id: str, sender: str, newest_key_id: str, hour_start: float
    ) -> None:
        """Record that a retiring key verified a frame from ``sender`` in this hour.

        Parameters
        ----------
        key_id : str
            The retiring key.
        sender : str
            The sender whose frame it verified; cut to the stored length.
        newest_key_id : str
            The newest key valid for that sender, the one meant to replace it.
        hour_start : float
            Start of the hour the row stands for, in POSIX seconds.
        """
        self._journal.append(
            MESSAGE_KEY_RETIRING_ADMISSION_KIND,
            {
                "key_id": key_id,
                "sender": sender[:AGENT_FIELD_LIMIT],
                "newest_key_id": newest_key_id,
                "hour_start": hour_start,
            },
            durable=True,
        )


def _start(entry: MessageAuthKeyEntry) -> float:
    """Return the entry's ``not_before`` for ordering; an open one is the earliest."""
    return float("-inf") if entry.not_before is None else entry.not_before


def newest_for_sender(
    entries: tuple[MessageAuthKeyEntry, ...], sender: str, now: float
) -> MessageAuthKeyEntry | None:
    """Return the newest key valid at ``now`` that ``sender`` may sign with.

    Parameters
    ----------
    entries : tuple of MessageAuthKeyEntry
        The set in force, in file order.
    sender : str
        The sender whose keys are compared.
    now : float
        The instant on the validity clock.

    Returns
    -------
    MessageAuthKeyEntry or None
        The valid key naming ``sender`` with the latest ``not_before``; of equal
        values, the one later in the file. ``None`` when there is none.
    """
    newest: MessageAuthKeyEntry | None = None
    for entry in entries:
        valid = (
            sender in entry.senders
            and (entry.not_before is None or now >= entry.not_before)
            and (entry.not_after is None or now < entry.not_after)
        )
        if valid and (newest is None or _start(entry) >= _start(newest)):
            newest = entry
    return newest


class MessageAuthKeyWatch:
    """Check one key set periodically and record its expiries and overlaps.

    Parameters
    ----------
    keys : MessageAuthKeySet
        The set the hub verifies signed frames against.
    audit : MessageAuthKeyAudit or None
        Journal writer; ``None`` records nothing.
    """

    def __init__(self, keys: MessageAuthKeySet, audit: MessageAuthKeyAudit | None) -> None:
        self._keys = keys
        self._audit = audit
        self._expired: set[str] = set()
        self._retiring = HourlyOnce(RETIRING_MEMORY_HOURS)
        keys.observe_lookups(lambda: self._record_expired("verification"))
        keys.observe_admissions(self._record_retiring)

    def check(self) -> None:
        """Re-read the files if they changed, then record keys that expired."""
        self._keys.refresh()
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
                _LOG.exception("cannot check the message-auth key-set files")

    def _record_expired(self, observed: str) -> None:
        """Write one row for each key in force that passed its end of validity."""
        if self._audit is None:
            return
        now = self._keys.now()
        for entry in self._keys.entries:
            not_after = entry.not_after
            if not_after is None or now < not_after or entry.key_id in self._expired:
                continue
            self._audit.expired(entry.key_id, not_after, observed)
            self._expired.add(entry.key_id)

    def _record_retiring(self, entry: MessageAuthKeyEntry, sender: str) -> None:
        """Write the first verification per hour by a retiring key for ``sender``."""
        if self._audit is None:
            return
        now = self._keys.now()
        newest = newest_for_sender(self._keys.entries, sender, now)
        if newest is None or newest.key_id == entry.key_id:
            return
        hour = hour_of(now)
        if not self._retiring.is_new((entry.key_id, sender), hour):
            return
        self._audit.retiring_admission(entry.key_id, sender, newest.key_id, hour * HOUR_SECONDS)
        self._retiring.mark((entry.key_id, sender), hour)


def message_auth_key_watch_for(
    keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None, journal: EventStore | None
) -> MessageAuthKeyWatch | None:
    """Return the watch of a key set, attaching its audit when there is a journal.

    Parameters
    ----------
    keys : Mapping, list or None
        The hub's per-message keys; only a
        :class:`~synapse_channel.core.message_auth_key_set.MessageAuthKeySet`
        has files to watch.
    journal : EventStore or None
        The hub's durable event store.

    Returns
    -------
    MessageAuthKeyWatch or None
        The watch, already observing the set; ``None`` for static keys.
    """
    if not isinstance(keys, MessageAuthKeySet):
        return None
    audit = None if journal is None else MessageAuthKeyAudit(journal)
    if audit is not None:
        keys.observe(audit)
    return MessageAuthKeyWatch(keys, audit)
