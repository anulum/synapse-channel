# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — governance metrics measured on our own repository
"""Governance metrics for claim, push, attestation and detection discipline.

The honest value is not "smarter multi-agent chat" but *disciplined
dispatch and attestation on a shared multi-writer workstation git*, which agent
frameworks and MCP/A2A do not own. That claim only holds if we can measure
it on ourselves. This module defines the four governance metrics precisely, as a
**pure** computation over a sequence of observed governance events:

- **Unclaimed-edit rate.** Edits to a path while no live claim was held,
  over all edits. Should trend to 0 under the claim discipline.
- **Ungated self-push rate.** Author self-pushes that a forbidding owner
  gate should have stopped, over all pushes made under a forbidding gate. Should
  be 0.
- **Unattested main-move rate.** ``origin/main`` advances with no
  exact-object audit artifact attesting them, over all main moves. Should be 0.
- **Maximum time to detect.** The worst latency, in seconds, between a claim
  violation occurring and being detected.

The module holds no state and performs no I/O: it computes metrics from events a
collector supplies. The collectors that observe the real fleet (git history for
main moves, the hub feed for edits and pushes) are a separate, non-``core``
concern; main moves are cleanly derivable from git today, while edits and pushes
await governance-event plumbing that does
not yet exist — so this core is the definition against which those collectors are
built, not a claim that all four are already instrumented.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class EditEvent:
    """One observed edit to a path, and whether a live claim covered it."""

    path: str
    held_live_claim: bool


@dataclass(frozen=True)
class PushEvent:
    """One observed push, for the self-push-past-a-forbidding-gate rate.

    Attributes
    ----------
    was_self_push : bool
        The author pushed their own change (rather than a non-author landing it).
    owner_gate_forbade_self_push : bool
        An owner-gate policy was in force that forbids the author self-pushing.
    """

    was_self_push: bool
    owner_gate_forbade_self_push: bool


@dataclass(frozen=True)
class MainMoveEvent:
    """One observed ``origin/main`` advance, and whether an artifact attested it."""

    had_exact_object_artifact: bool


@dataclass(frozen=True)
class ClaimViolationEvent:
    """One detected claim violation and how long detection took, in seconds."""

    time_to_detect_seconds: float


#: The governance events this module computes metrics from.
GovernanceEvent = EditEvent | PushEvent | MainMoveEvent | ClaimViolationEvent


@dataclass(frozen=True)
class GovernanceMetrics:
    """The four computed governance metrics with the counts behind them.

    A rate is ``0.0`` when its denominator is zero (no relevant events), so an
    absent signal reads as "nothing wrong observed", never as a divide error.
    ``clean`` is ``True`` only when every rate is 0 and no claim violation was
    seen — the posture the discipline targets.
    """

    unclaimed_edit_rate: float
    ungated_self_push_rate: float
    unattested_main_move_rate: float
    max_time_to_detect_seconds: float
    total_edits: int
    total_forbidding_pushes: int
    total_main_moves: int
    total_claim_violations: int

    @property
    def clean(self) -> bool:
        """Return whether every rate is zero and no claim violation was observed."""
        return (
            self.unclaimed_edit_rate == 0.0
            and self.ungated_self_push_rate == 0.0
            and self.unattested_main_move_rate == 0.0
            and self.total_claim_violations == 0
        )


def _rate(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator``, or ``0.0`` when the denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_governance_metrics(events: Sequence[GovernanceEvent]) -> GovernanceMetrics:
    """Compute the four governance metrics from a sequence of governance events.

    Parameters
    ----------
    events : Sequence[GovernanceEvent]
        The observed events, in any order; unknown event types are ignored so a
        richer collector can pass a superset without breaking this computation.

    Returns
    -------
    GovernanceMetrics
        The four metrics and their supporting counts.
    """
    total_edits = 0
    unclaimed_edits = 0
    forbidding_pushes = 0
    ungated_self_pushes = 0
    total_main_moves = 0
    unattested_main_moves = 0
    claim_violations = 0
    max_time_to_detect = 0.0

    for event in events:
        if isinstance(event, EditEvent):
            total_edits += 1
            if not event.held_live_claim:
                unclaimed_edits += 1
        elif isinstance(event, PushEvent):
            if event.owner_gate_forbade_self_push:
                forbidding_pushes += 1
                if event.was_self_push:
                    ungated_self_pushes += 1
        elif isinstance(event, MainMoveEvent):
            total_main_moves += 1
            if not event.had_exact_object_artifact:
                unattested_main_moves += 1
        elif isinstance(event, ClaimViolationEvent):
            claim_violations += 1
            max_time_to_detect = max(max_time_to_detect, event.time_to_detect_seconds)

    return GovernanceMetrics(
        unclaimed_edit_rate=_rate(unclaimed_edits, total_edits),
        ungated_self_push_rate=_rate(ungated_self_pushes, forbidding_pushes),
        unattested_main_move_rate=_rate(unattested_main_moves, total_main_moves),
        max_time_to_detect_seconds=max_time_to_detect,
        total_edits=total_edits,
        total_forbidding_pushes=forbidding_pushes,
        total_main_moves=total_main_moves,
        total_claim_violations=claim_violations,
    )


__all__ = [
    "EditEvent",
    "PushEvent",
    "MainMoveEvent",
    "ClaimViolationEvent",
    "GovernanceEvent",
    "GovernanceMetrics",
    "compute_governance_metrics",
]
