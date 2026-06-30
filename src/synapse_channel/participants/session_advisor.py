# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — turn session telemetry into advisory operational signals
"""Turn a session's running telemetry into advisory operational signals.

This is the meta-decision layer the operational telemetry exists for: it reads
:class:`~synapse_channel.participants.session_telemetry.SessionMetrics` and a small set of
thresholds and reports what an orchestrator might *consider* doing — write a log, compact a filling
context, ease off a provider near its rate limit, stop against a budget, or look into a run that is
erroring a lot.

Crucially these are **advice, not actions, and evidence, not a gate** — the same stance the hub's
budget accounting takes. :func:`assess_session` is a pure function that never writes a log, never
compacts, and never stops a run; it returns recommendations with reasons so a human or a higher
layer decides whether to act. Automatic action, if ever wanted, is a separate opt-in step built on
top of this evidence, never folded into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from synapse_channel.participants.session_telemetry import SessionMetrics


class SessionSignal(str, Enum):
    """A kind of operational advice. The value is a stable lowercase string for logs and notes."""

    COMPACT_SOON = "compact-soon"
    LOG_NOW = "log-now"
    OVER_BUDGET = "over-budget"
    APPROACHING_RATE_LIMIT = "approaching-rate-limit"
    HIGH_ERROR_RATE = "high-error-rate"


@dataclass(frozen=True)
class AdvisorThresholds:
    """Knobs that decide when each advisory signal fires.

    A threshold of ``0`` (or ``None`` for the budget) disables its check, so an orchestrator opts
    into exactly the advice it wants.

    Attributes
    ----------
    context_window_tokens : int
        The model's context window; the current context (the last turn's input tokens) is compared
        against it. ``0`` disables the compaction check.
    compact_at_fraction : float
        Fraction of the context window at which a compaction is advised.
    log_every_turns : int
        Advise a log every this many turns; ``0`` disables the log cadence check.
    budget_usd : float or None
        Cumulative spend ceiling; reaching it advises stopping. ``None`` disables the check.
    rate_limit_warn_at : float
        Rate-limit utilisation at or above which a provider is flagged as near its limit.
    error_rate_warn_at : float
        Turn error rate at or above which the run is flagged, once enough turns have run.
    min_turns_for_error_rate : int
        Minimum turns before the error-rate check fires, so one early failure is not over-read.
    """

    context_window_tokens: int = 0
    compact_at_fraction: float = 0.8
    log_every_turns: int = 0
    budget_usd: float | None = None
    rate_limit_warn_at: float = 0.85
    error_rate_warn_at: float = 0.5
    min_turns_for_error_rate: int = 3


@dataclass(frozen=True)
class Recommendation:
    """One advisory signal with the reason it fired."""

    signal: SessionSignal
    reason: str


@dataclass(frozen=True)
class SessionAdvice:
    """The advisory signals a session's telemetry raised.

    Attributes
    ----------
    recommendations : tuple[Recommendation, ...]
        Every signal that fired, each with its reason; empty when nothing is advised.
    """

    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """Return whether no advice was raised."""
        return not self.recommendations

    @property
    def signals(self) -> frozenset[SessionSignal]:
        """Return the set of signals that fired."""
        return frozenset(rec.signal for rec in self.recommendations)


def assess_session(metrics: SessionMetrics, thresholds: AdvisorThresholds) -> SessionAdvice:
    """Return the advisory signals a session's telemetry raises against ``thresholds``.

    Parameters
    ----------
    metrics : SessionMetrics
        The session's running operational totals.
    thresholds : AdvisorThresholds
        The thresholds deciding when each signal fires; a disabled check raises nothing.

    Returns
    -------
    SessionAdvice
        Every signal that fired with its reason, as evidence for a human or a higher layer — never
        an action taken here.
    """
    recommendations: list[Recommendation] = []

    if thresholds.context_window_tokens > 0:
        limit = thresholds.compact_at_fraction * thresholds.context_window_tokens
        if metrics.last_input_tokens >= limit:
            fraction = metrics.last_input_tokens / thresholds.context_window_tokens
            recommendations.append(
                Recommendation(
                    SessionSignal.COMPACT_SOON,
                    f"context {metrics.last_input_tokens} of "
                    f"{thresholds.context_window_tokens} tokens ({fraction:.0%}) — compaction due",
                )
            )

    if thresholds.log_every_turns > 0 and metrics.turns > 0:
        if metrics.turns % thresholds.log_every_turns == 0:
            recommendations.append(
                Recommendation(
                    SessionSignal.LOG_NOW,
                    f"{metrics.turns} turns reached (every {thresholds.log_every_turns}) — log due",
                )
            )

    if thresholds.budget_usd is not None and metrics.cost_usd >= thresholds.budget_usd:
        recommendations.append(
            Recommendation(
                SessionSignal.OVER_BUDGET,
                f"spend {metrics.cost_usd:.4f} reached budget {thresholds.budget_usd:.4f}",
            )
        )

    utilisation = metrics.max_rate_limit_utilisation
    if utilisation is not None and utilisation >= thresholds.rate_limit_warn_at:
        recommendations.append(
            Recommendation(
                SessionSignal.APPROACHING_RATE_LIMIT,
                f"rate-limit utilisation {utilisation:.0%} at or above "
                f"{thresholds.rate_limit_warn_at:.0%}",
            )
        )

    if (
        metrics.turns >= thresholds.min_turns_for_error_rate
        and metrics.error_rate >= thresholds.error_rate_warn_at
    ):
        recommendations.append(
            Recommendation(
                SessionSignal.HIGH_ERROR_RATE,
                f"{metrics.errors} of {metrics.turns} turns errored "
                f"({metrics.error_rate:.0%}) — investigate",
            )
        )

    return SessionAdvice(recommendations=tuple(recommendations))
