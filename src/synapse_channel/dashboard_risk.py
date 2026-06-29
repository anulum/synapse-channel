# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator risk view derived from fleet visibility
"""Turn the read-only fleet snapshot into an operator risk view.

The dashboard already exposes everything an operator needs to judge fleet health
— stale leases, advisory branch conflicts, blocked tasks, and the ready queue —
but as separate lists they leave the reader to do the triage. This module does
that triage: it folds those signals into a single red / amber / green verdict, a
priority-ordered signal list (the things to look at, worst first), and a
**safe next work** queue (the ready tasks an operator can pick up now).

It derives strictly from :class:`~synapse_channel.dashboard_fleet.FleetVisibility`
and invents nothing: every signal points back to a concrete stale lease, conflict
candidate, or blocked task already in the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.dashboard_fleet import FleetVisibility, JsonDict

GREEN = "green"
"""Risk level: nothing in the snapshot needs operator attention."""

AMBER = "amber"
"""Risk level: progress is impeded (blocked work) but nothing is unsafe."""

RED = "red"
"""Risk level: an unsafe condition (stale lease or branch conflict) is present."""

STALE_CLAIM = "stale_claim"
"""Signal category for an expired lease still recorded as held."""

BRANCH_CONFLICT = "branch_conflict"
"""Signal category for two active claims whose branches may collide."""

BLOCKED_TASK = "blocked_task"
"""Signal category for a task waiting on unmet dependencies."""

_LEVEL_RANK = {RED: 0, AMBER: 1, GREEN: 2}


@dataclass(frozen=True)
class RiskSignal:
    """One triaged risk pointing back to a concrete snapshot record.

    Attributes
    ----------
    level : str
        ``red`` or ``amber``; green is the absence of signals, not a signal.
    category : str
        One of ``stale_claim``, ``branch_conflict``, or ``blocked_task``.
    subject : str
        The task id, owner pair, or other handle the signal is about.
    detail : str
        A short human-readable explanation of the condition.
    """

    level: str
    category: str
    subject: str
    detail: str

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "level": self.level,
            "category": self.category,
            "subject": self.subject,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RiskView:
    """Operator triage derived from fleet visibility.

    Attributes
    ----------
    level : str
        Overall verdict: the worst signal level, or ``green`` when there are none.
    signals : list[RiskSignal]
        Risks ordered worst-first, then by category and subject.
    safe_next_work : list[str]
        Ready task ids an operator can pick up now.
    """

    level: str
    signals: list[RiskSignal]
    safe_next_work: list[str]

    def counts(self) -> dict[str, int]:
        """Return the number of signals at each level plus the safe-work count."""
        return {
            RED: sum(1 for signal in self.signals if signal.level == RED),
            AMBER: sum(1 for signal in self.signals if signal.level == AMBER),
            "safe_next_work": len(self.safe_next_work),
        }

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible mapping."""
        return {
            "level": self.level,
            "signals": [signal.to_dict() for signal in self.signals],
            "safe_next_work": self.safe_next_work,
            "counts": self.counts(),
        }


def _stale_claim_signal(claim: JsonDict) -> RiskSignal:
    """Build a red signal for an expired lease still recorded as held."""
    owner = str(claim.get("owner", "")) or "an agent"
    task_id = str(claim.get("task_id", "")) or "(unnamed)"
    paths = claim.get("paths")
    scope = ", ".join(paths) if isinstance(paths, list) and paths else "its claimed scope"
    return RiskSignal(
        level=RED,
        category=STALE_CLAIM,
        subject=task_id,
        detail=f"lease held by {owner} on {scope} has expired but is still recorded",
    )


def _branch_conflict_signal(conflict: JsonDict) -> RiskSignal:
    """Build a red signal for two active claims whose branches may collide."""
    owner_a = str(conflict.get("owner_a", "")) or "agent A"
    owner_b = str(conflict.get("owner_b", "")) or "agent B"
    detail = str(conflict.get("description", "")) or "active claims may collide on shared paths"
    return RiskSignal(
        level=RED,
        category=BRANCH_CONFLICT,
        subject=f"{owner_a} vs {owner_b}",
        detail=detail,
    )


def _blocked_task_signal(blocked: JsonDict) -> RiskSignal:
    """Build an amber signal for a task waiting on unmet dependencies."""
    task_id = str(blocked.get("task_id", "")) or "(unnamed)"
    blocked_by = blocked.get("blocked_by")
    deps = (
        ", ".join(blocked_by)
        if isinstance(blocked_by, list) and blocked_by
        else "unmet dependencies"
    )
    return RiskSignal(
        level=AMBER,
        category=BLOCKED_TASK,
        subject=task_id,
        detail=f"blocked on {deps}",
    )


def _overall_level(signals: list[RiskSignal]) -> str:
    """Return the worst level among signals, or green when there are none."""
    if any(signal.level == RED for signal in signals):
        return RED
    if any(signal.level == AMBER for signal in signals):
        return AMBER
    return GREEN


def build_risk_view(fleet: FleetVisibility) -> RiskView:
    """Derive an operator risk view from fleet visibility.

    Parameters
    ----------
    fleet : FleetVisibility
        The derived fleet snapshot to triage.

    Returns
    -------
    RiskView
        The overall verdict, ordered signals, and the safe-next-work queue.
    """
    signals = [_stale_claim_signal(claim) for claim in fleet.claims.stale_claims]
    signals.extend(_branch_conflict_signal(conflict) for conflict in fleet.branch_conflicts)
    signals.extend(_blocked_task_signal(blocked) for blocked in fleet.tasks.blocked)
    signals.sort(key=lambda signal: (_LEVEL_RANK[signal.level], signal.category, signal.subject))
    return RiskView(
        level=_overall_level(signals),
        signals=signals,
        safe_next_work=list(fleet.tasks.ready),
    )
