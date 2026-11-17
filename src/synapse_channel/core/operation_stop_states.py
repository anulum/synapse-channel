# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the state of a stop, computed from its records, and its profile
"""Compute the state of a recorded stop and the profile its report states.

A stop is in one of three states, each computed from kept records only:

* ``stop requested``: the stop is recorded;
* ``no new effect``: in addition, every attempt it listed has a kept
  ``sealed`` or ``applied`` read-back. An executor's own report is never read;
* ``at rest``: in addition, every listed attempt is reconciled: an identity
  other than the declarer recorded an observation after the attempt ended, or
  verified the operation.

A stop that listed nothing is at rest when it is recorded.

Every report states its profile. Today it is always ``cooperative``: "no new
effect" then means no governed effect, and a writer that does not ask the hub
is not stopped. ``isolated`` would need every listed intent to require it and
the isolated profile's evidence that the process group is empty; the hub
keeps no such evidence, so it is never written.

This module is pure; the ledger supplies the facts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

STATE_STOP_REQUESTED = "stop requested"
STATE_NO_NEW_EFFECT = "no new effect"
STATE_AT_REST = "at rest"
STOP_STATES = (STATE_STOP_REQUESTED, STATE_NO_NEW_EFFECT, STATE_AT_REST)
"""The states of a recorded stop, in the order a stop advances through them."""

PROFILE_COOPERATIVE = "cooperative"
PROFILE_ISOLATED = "isolated"
PROFILE_SAYS = {
    PROFILE_COOPERATIVE: (
        "governed effects only: a writer that does not ask the hub is not stopped by this stop"
    ),
    PROFILE_ISOLATED: "no effect from the listed attempts' processes, which were read as gone",
}
"""What each profile lets a report say, in the report's own words."""

REASON_UNSEALABLE = "unsealable"
REASON_UNREAD = "unread"
REASON_UNKNOWN = "unknown: no marker read back"
REASON_AWAITING = "awaiting an independent observation"
"""Why one listed attempt holds its stop back."""

COUNT_NAMES = ("listed", "sealed", "applied", "unsealable", "unread", "reconciled")


@dataclass(frozen=True)
class ListedAttempt:
    """The facts of one listed attempt that its stop's state is computed from.

    Attributes
    ----------
    operation_id, attempt_id : str
        The attempt.
    readback : str or None
        The latest kept read-back result for this stop, or ``None``.
    reconciled : bool
        Whether an identity other than the declarer observed it after it ended,
        or verified its operation.
    """

    operation_id: str
    attempt_id: str
    readback: str | None
    reconciled: bool


@dataclass(frozen=True)
class StopState:
    """The computed state of one stop, with its profile, blockers and counts."""

    state: str
    profile: str
    blocking: tuple[tuple[str, str, str], ...] = ()
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def profile_says(self) -> str:
        """Return what the profile lets the report say."""
        return PROFILE_SAYS[self.profile]

    def rank(self) -> int:
        """Return the state's place in :data:`STOP_STATES`."""
        return STOP_STATES.index(self.state)

    def to_payload(self) -> dict[str, Any]:
        """Return the report's fields, as a row and an answer carry them."""
        return {
            "state": self.state,
            "profile": self.profile,
            "profile_says": self.profile_says,
            "blocking": [
                {"operation_id": operation_id, "attempt_id": attempt_id, "reason": reason}
                for operation_id, attempt_id, reason in self.blocking
            ],
            "counts": dict(self.counts),
        }


def profile_for(assurances: Iterable[str | None], *, isolation_evidence: bool) -> str:
    """Return the profile a stop's report may state.

    ``isolated`` needs every listed intent to require ``isolated`` and the
    isolated profile's evidence; anything less is ``cooperative``.
    """
    required = list(assurances)
    if isolation_evidence and required and all(item == PROFILE_ISOLATED for item in required):
        return PROFILE_ISOLATED
    return PROFILE_COOPERATIVE


def compute_stop_state(
    listed: Iterable[ListedAttempt], *, closing: frozenset[str], profile: str
) -> StopState:
    """Return the state of a stop from the facts of the attempts it listed.

    Parameters
    ----------
    listed : Iterable[ListedAttempt]
        Each listed attempt's latest read-back and whether it is reconciled.
    closing : frozenset[str]
        The read-back results that close an attempt for its stop.
    profile : str
        The profile the report states.
    """
    attempts = list(listed)
    counts = dict.fromkeys(COUNT_NAMES, 0)
    counts["listed"] = len(attempts)
    blocking: list[tuple[str, str, str]] = []
    open_for_stop = awaiting = False
    for item in attempts:
        if item.readback is not None and item.readback in counts:
            counts[item.readback] += 1
        counts["reconciled"] += item.reconciled
        if item.readback not in closing:
            open_for_stop = True
            reason = (
                item.readback
                if item.readback in (REASON_UNSEALABLE, REASON_UNREAD)
                else REASON_UNKNOWN
            )
            blocking.append((item.operation_id, item.attempt_id, reason))
        elif not item.reconciled:
            awaiting = True
            blocking.append((item.operation_id, item.attempt_id, REASON_AWAITING))
    if open_for_stop:
        state = STATE_STOP_REQUESTED
    elif awaiting:
        state = STATE_NO_NEW_EFFECT
    else:
        state = STATE_AT_REST
    return StopState(state, profile, tuple(blocking), counts)
