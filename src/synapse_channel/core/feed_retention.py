# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — feed acknowledgements as a retention floor
"""Read the feed acknowledgements and compaction floors a journal records.

A **registered feed consumer** is one that holds at least one acknowledgement
in the journal's current instance. Each such consumer's highest
acknowledgement is its durable statement of what it has ingested, and the
lowest of them is the floor below which compaction may delete without losing a
row any registered consumer still needs (:func:`feed_ack_floor`). Compaction
records the floor it applied (:func:`~synapse_channel.core.journal.record_compaction_floor`);
the feed reports the highest one as ``compaction_floor`` and tells a consumer
whose cursor lies below it that rows there may be gone
(:func:`applied_compaction_floor`).

Acknowledgements and floors of another journal instance never count: a
restored or promoted journal starts with no registered consumer. A consumer
the operator released by name in a compaction of the instance is not
registered again until it acknowledges after that release, so a release
holds in every later run.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.persistence_store_format import JournalInstance

RETENTION_SCAN_PAGE = 1000
"""Rows one read of acknowledgement or floor events returns."""


class RetentionJournal(Protocol):
    """The journal reads the retention floor makes."""

    def read_since(
        self, after_seq: int, *, kinds: Iterable[str] | None = None, limit: int | None = None
    ) -> list[StoredEvent]:
        """Return events whose sequence is greater than a cursor, in order."""

    def journal_instance(self) -> JournalInstance | None:
        """Return the journal instance, or ``None`` when the store has none."""


@dataclass(frozen=True)
class FeedAckFloor:
    """The retention floor the registered feed consumers hold.

    Attributes
    ----------
    floor : int
        The lowest of the consumers' highest acknowledgements.
    consumers : tuple of (str, int)
        Each registered consumer with its highest acknowledgement, by name.
    """

    floor: int
    consumers: tuple[tuple[str, int], ...]

    def holding(self, requested: int) -> tuple[tuple[str, int], ...]:
        """Return the consumers whose acknowledgement lies below ``requested``."""
        return tuple(item for item in self.consumers if item[1] < requested)


def _events(
    journal: RetentionJournal, kinds: tuple[str, ...], after_seq: int = 0
) -> Iterator[StoredEvent]:
    """Yield every retained event of ``kinds`` above ``after_seq`` in order, a page at a time."""
    after = after_seq
    while True:
        page = journal.read_since(after, kinds=kinds, limit=RETENTION_SCAN_PAGE)
        yield from page
        if len(page) < RETENTION_SCAN_PAGE:
            return
        after = page[-1].seq


def consumer_acks(journal: RetentionJournal, instance: str) -> dict[str, int]:
    """Return each consumer's highest acknowledgement in ``instance``.

    Parameters
    ----------
    journal : RetentionJournal
        The journal to read.
    instance : str
        The journal instance whose acknowledgements count.

    Returns
    -------
    dict[str, int]
        Highest acknowledged sequence by consumer; a row of another instance, or
        one whose fields are not a text consumer and an integer, is ignored. A
        consumer released by name in a compaction of the instance is left out
        until an acknowledgement after that release registers it again.
    """
    highest: dict[str, int] = {}
    for event in _events(journal, (EventKind.FEED_ACK, EventKind.COMPACTION_FLOOR)):
        payload = event.payload
        if payload.get("journal_instance") != instance:
            continue
        if event.kind == EventKind.COMPACTION_FLOOR:
            released = payload.get("released")
            for name in released if isinstance(released, list) else ():
                if isinstance(name, str):
                    highest.pop(name, None)
            continue
        consumer, acked = payload.get("consumer"), payload.get("acked_seq")
        if isinstance(consumer, str) and type(acked) is int:
            highest[consumer] = max(highest.get(consumer, 0), acked)
    return highest


def consumer_bound(journal: RetentionJournal, consumer: str, instance: str) -> bool:
    """Return whether ``consumer`` already bound a legacy position in ``instance``.

    Parameters
    ----------
    journal : RetentionJournal
        The journal to read.
    consumer : str
        The consumer.
    instance : str
        The journal instance.

    Returns
    -------
    bool
        Whether a ``feed_bind`` row of the consumer in the instance is retained.
    """
    return any(
        event.payload.get("consumer") == consumer
        and event.payload.get("journal_instance") == instance
        for event in _events(journal, (EventKind.FEED_BIND,))
    )


def feed_ack_floor(journal: RetentionJournal) -> FeedAckFloor | None:
    """Return the floor the registered feed consumers hold, or ``None`` without any.

    Parameters
    ----------
    journal : RetentionJournal
        The journal to read.

    Returns
    -------
    FeedAckFloor or None
        The lowest highest acknowledgement and every registered consumer, or
        ``None`` when the journal has no instance or no acknowledgement in it.
    """
    instance = journal.journal_instance()
    if instance is None:
        return None
    acks = consumer_acks(journal, instance.instance)
    if not acks:
        return None
    return FeedAckFloor(floor=min(acks.values()), consumers=tuple(sorted(acks.items())))


def applied_compaction_floor(
    journal: RetentionJournal, instance: str, *, after_seq: int = 0
) -> int:
    """Return the highest floor a compaction applied in ``instance``, or ``0``.

    Parameters
    ----------
    journal : RetentionJournal
        The journal to read.
    instance : str
        The journal instance whose compactions count.
    after_seq : int, optional
        Read only the floor events above this sequence; the feed ledger passes
        the sequence it has already read through.

    Returns
    -------
    int
        The highest ``applied_floor`` recorded for the instance above ``after_seq``.

    Notes
    -----
    Compaction never deletes a floor event unless it is a quarantined row, and a
    run that drops one records its own floor at or above that row's sequence, so
    the highest floor survives every compaction.
    """
    highest = 0
    for event in _events(journal, (EventKind.COMPACTION_FLOOR,), after_seq):
        applied = event.payload.get("applied_floor")
        if event.payload.get("journal_instance") == instance and type(applied) is int:
            highest = max(highest, applied)
    return highest
