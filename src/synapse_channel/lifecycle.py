# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — typed task-status lifecycle and legal transitions
"""Typed lifecycle for a claimed task's status.

Free-form status strings let an agent jump a task to any value, including
nonsense or a state it can no longer be in (``done`` then ``working``). This
module defines the small A2A-shaped lifecycle the bus enforces instead: a task is
``claimed`` when leased, moves through ``working`` and optionally
``input_required``, and ends ``done`` or ``failed``. Terminal states accept no
further transition. The transition table is the single source of truth the state
registry consults when an owner updates a task.
"""

from __future__ import annotations


class TaskStatus:
    """The legal status values for a claimed task.

    ``CLAIMED`` is the entry state stamped when a lease is granted; ``DONE`` and
    ``FAILED`` are terminal. Values are the literal strings carried on the wire.
    """

    CLAIMED = "claimed"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    DONE = "done"
    FAILED = "failed"


ALL_STATUSES = frozenset(
    {
        TaskStatus.CLAIMED,
        TaskStatus.WORKING,
        TaskStatus.INPUT_REQUIRED,
        TaskStatus.DONE,
        TaskStatus.FAILED,
    }
)
"""Every recognised status value."""

TERMINAL_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.FAILED})
"""Statuses that accept no further transition."""

_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.CLAIMED: frozenset({TaskStatus.WORKING, TaskStatus.DONE, TaskStatus.FAILED}),
    TaskStatus.WORKING: frozenset({TaskStatus.INPUT_REQUIRED, TaskStatus.DONE, TaskStatus.FAILED}),
    TaskStatus.INPUT_REQUIRED: frozenset({TaskStatus.WORKING, TaskStatus.DONE, TaskStatus.FAILED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def is_known(status: str) -> bool:
    """Return whether ``status`` is a recognised lifecycle value."""
    return status in ALL_STATUSES


def is_terminal(status: str) -> bool:
    """Return whether ``status`` is a terminal (done/failed) state."""
    return status in TERMINAL_STATUSES


def can_transition(current: str, target: str) -> bool:
    """Return whether a task may move from ``current`` to ``target``.

    A move to an unknown status is never allowed. Re-affirming the same status is
    always allowed (it lets an owner update other fields without changing state).
    Otherwise the move must appear in the transition table.

    Parameters
    ----------
    current : str
        The task's present status.
    target : str
        The requested next status.

    Returns
    -------
    bool
        ``True`` if the transition is legal.
    """
    if target not in ALL_STATUSES:
        return False
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())
