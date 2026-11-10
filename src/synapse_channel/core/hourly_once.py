# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — one audit row per subject and hour, with a bounded memory
"""Decide whether an event for a subject is the first of its hour.

Audit rows such as an admission by a retiring credential are written once per
subject and hour, so repeated use shows without flooding the journal. A
validity clock may step back, so the hours already recorded for a subject are
kept: an hour recorded before writes nothing again. The memory is bounded per
subject to the ``memory_hours`` behind the latest recorded hour; an event in an
hour further back than that writes nothing, its use being shown by the later
rows. The memory belongs to one process; a restarted process starts empty.
"""

from __future__ import annotations

from collections.abc import Hashable

HOUR_SECONDS = 3600.0
"""Length of one hour on the POSIX clock."""


def hour_of(now: float) -> int:
    """Return the index of the POSIX hour that contains ``now``."""
    return int(now // HOUR_SECONDS)


class HourlyOnce:
    """Remember, per subject, the hours already recorded.

    Parameters
    ----------
    memory_hours : int
        How many hours behind a subject's latest recorded hour are kept.
    """

    def __init__(self, memory_hours: int) -> None:
        self._memory_hours = memory_hours
        self._recorded: dict[Hashable, set[int]] = {}

    def is_new(self, subject: Hashable, hour: int) -> bool:
        """Whether an event of ``subject`` in ``hour`` still needs its row."""
        recorded = self._recorded.get(subject)
        if not recorded:
            return True
        return hour not in recorded and hour > max(recorded) - self._memory_hours

    def mark(self, subject: Hashable, hour: int) -> None:
        """Record that ``hour`` has its row for ``subject`` and drop hours out of memory."""
        recorded = self._recorded.setdefault(subject, set())
        recorded.add(hour)
        floor = max(recorded) - self._memory_hours
        recorded.difference_update({kept for kept in recorded if kept <= floor})
