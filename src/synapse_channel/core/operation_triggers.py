# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the triggers that lower a scope's level, and the rows of one step down
"""Name the triggers that lower a scope's autonomy level, and build the rows of one.

A trigger is recorded in the same transaction as the step down it causes
(``operation_trigger`` and each ``operation_level`` it writes, cause
``trigger:<seq>``), so no approval is admitted and no attempt opened between
them. The hub writes four triggers:

* ``incorrect_acceptance``: an independent verification reads ``not_verified``
  an operation an earlier independent verification read ``verified``;
* ``deadline_unverified``: the hub's watcher finds, at its tick, an operation
  past its intent's deadline with an ended attempt and no independent
  verification;
* ``stop_unsettled``: the watcher finds a stop still ``stop requested`` when
  the hub's stop bound has passed since it was recorded;
* ``actor_request``: an actor of a scope asks for its own step down.

The first three lower to A1: an operation's trigger lowers every scope that
contains it and stands above A1, a stop's lowers the stopped scope. An actor's
request lowers the scope it names to the level it names. A scope already at
the target gets the trigger row and no level change. This module is pure; the
hub decides and writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_levels import DEFAULT_LEVEL, rank

CAUSE_INCORRECT_ACCEPTANCE = "incorrect_acceptance"
CAUSE_DEADLINE_UNVERIFIED = "deadline_unverified"
CAUSE_STOP_UNSETTLED = "stop_unsettled"
CAUSE_ACTOR_REQUEST = "actor_request"
CAUSES = (
    CAUSE_INCORRECT_ACCEPTANCE,
    CAUSE_DEADLINE_UNVERIFIED,
    CAUSE_STOP_UNSETTLED,
    CAUSE_ACTOR_REQUEST,
)
"""The triggers Core can see now; the ladder's other three wait for their sources."""

TRIGGER_TARGET = DEFAULT_LEVEL
"""The level the hub's own triggers lower to."""

WATCH_INTERVAL_SECONDS = 15.0
"""Seconds between two ticks of the hub's trigger watcher."""

LOWERED = "lowered"
NOT_AN_ACTOR = "not_an_actor"
NOT_LOWER = "not_lower"
LOWER_ANSWERS = (LOWERED, NOT_AN_ACTOR, NOT_LOWER)
"""Every answer to an actor's request for its own step down, each journalled."""


@dataclass(frozen=True)
class Trigger:
    """One trigger the hub is about to record, with the scopes it lowers.

    Attributes
    ----------
    cause : str
        One of :data:`CAUSES`.
    scope : str
        The scope the trigger is about: an operation's scope, a stop's scope, or
        the scope an actor named.
    target : str
        The level the step down goes to.
    lowers : tuple of (str, str, int)
        Each scope to lower, with its current level and epoch, narrowest first.
    shown_by : int
        The journal sequence of the record that shows the trigger; 0 when the
        hub's own tick saw it.
    by : str
        The actor that asked for it, or empty for the hub's own triggers.
    subject : Mapping[str, Any]
        ``operation_id`` or ``stop_seq``, so that a restart writes no trigger twice.
    """

    cause: str
    scope: str
    target: str
    lowers: tuple[tuple[str, str, int], ...]
    shown_by: int = 0
    by: str = ""
    subject: Mapping[str, Any] = field(default_factory=dict)


def above(scopes: Iterable[tuple[str, str, int]], target: str) -> tuple[tuple[str, str, int], ...]:
    """Return the ``(scope, level, epoch)`` entries whose level stands above ``target``."""
    return tuple(entry for entry in scopes if rank(entry[1]) > rank(target))


def trigger_row(trigger: Trigger, now: float) -> dict[str, Any]:
    """Return the ``operation_trigger`` row of ``trigger``."""
    return {
        "scope": trigger.scope,
        "cause": trigger.cause,
        "target": trigger.target,
        "shown_by": trigger.shown_by,
        "by": trigger.by,
        "lowered": [scope for scope, _level, _epoch in trigger.lowers],
        **dict(trigger.subject),
        "hub_time": now,
    }


def level_rows(trigger: Trigger, trigger_seq: int, now: float) -> list[tuple[str, dict[str, Any]]]:
    """Return the ``operation_level`` rows of a trigger journalled at ``trigger_seq``."""
    return [
        (
            EventKind.OPERATION_LEVEL,
            {
                "scope": scope,
                "level": trigger.target,
                "from": level,
                "cause": f"trigger:{trigger_seq}",
                "granted_by": trigger.by,
                "epoch": epoch + 1,
                "hub_time": now,
            },
        )
        for scope, level, epoch in trigger.lowers
    ]


def ladder_refusal(scope: str) -> str:
    """Return the refusal of an automated approval of an operation at A1 under ``--ladder``."""
    return (
        "approval gate: an automated approval needs level A2 or above "
        f"(scope {scope} is at {DEFAULT_LEVEL})"
    )
