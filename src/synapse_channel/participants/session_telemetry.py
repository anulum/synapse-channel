# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — running operational telemetry for a participant session
"""Accumulate operational telemetry across the turns of a participant session.

Where the usage accounting answers *what a model cost*, this answers *how a session is going*:
its turn count, error and abstention rates, cumulative token pressure, spend, and latency. These
operational figures are what an advisor reads to make meta-decisions — when to write a log, when a
context is filling and a compaction is due, when a run is approaching a budget or a rate limit.

:class:`SessionMetrics` is an immutable running total; :func:`accumulate` folds one finished
:class:`~synapse_channel.participants.envelope.TurnResult` into it with the turn's measured latency.
The fold is pure — the caller measures wall-clock around the turn and passes it in — so the running
state is deterministic and testable without a clock.

Scope note: the cumulative token figures are the **driven participants'** token pressure, the
honest signal this layer can see. The orchestrator's *own* remaining context window is a
harness-level metric this layer does not observe; an advisor treats the cumulative participant
tokens as a proxy for context pressure, not as the orchestrator's exact remaining window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.participants.envelope import TurnResult


@dataclass(frozen=True)
class SessionMetrics:
    """Running operational totals across a session's turns.

    Attributes
    ----------
    turns : int
        Number of turns folded in so far.
    errors : int
        Turns that ended in an error result.
    abstentions : int
        Turns that abstained (no error, no answer).
    input_tokens : int
        Cumulative prompt/input tokens across turns.
    output_tokens : int
        Cumulative completion/output tokens across turns.
    cost_usd : float
        Cumulative metered spend across turns.
    total_latency_seconds : float
        Cumulative wall-clock time spent in turns.
    max_rate_limit_utilisation : float or None
        The highest rate-limit utilisation observed on any turn, or ``None`` when never reported.
    last_input_tokens : int
        The most recent turn's input tokens — the current context size, used as the context-pressure
        signal (the cumulative figure overcounts, since each turn re-sends its history).
    """

    turns: int = 0
    errors: int = 0
    abstentions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    total_latency_seconds: float = 0.0
    max_rate_limit_utilisation: float | None = None
    last_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Return the sum of cumulative input and output tokens."""
        return self.input_tokens + self.output_tokens

    @property
    def mean_latency_seconds(self) -> float:
        """Return the mean turn latency, or ``0.0`` before any turn."""
        return self.total_latency_seconds / self.turns if self.turns else 0.0

    @property
    def error_rate(self) -> float:
        """Return the fraction of turns that errored, or ``0.0`` before any turn."""
        return self.errors / self.turns if self.turns else 0.0


def accumulate(
    metrics: SessionMetrics, result: TurnResult, *, latency_seconds: float
) -> SessionMetrics:
    """Fold one finished turn into the running session metrics.

    Parameters
    ----------
    metrics : SessionMetrics
        The running totals so far (use the default :class:`SessionMetrics` to start).
    result : TurnResult
        The finished turn to fold in; supplies tokens, cost, error/abstain state, and the
        turn's rate-limit utilisation.
    latency_seconds : float
        Wall-clock time the turn took, measured by the caller. Negative input is clamped to zero
        so a bad measurement cannot make the cumulative latency run backwards.

    Returns
    -------
    SessionMetrics
        A new totals object with this turn folded in.
    """
    latency = max(0.0, latency_seconds)
    return SessionMetrics(
        turns=metrics.turns + 1,
        errors=metrics.errors + (1 if result["is_error"] else 0),
        abstentions=metrics.abstentions + (1 if result["abstained"] else 0),
        input_tokens=metrics.input_tokens + result["input_tokens"],
        output_tokens=metrics.output_tokens + result["output_tokens"],
        cost_usd=metrics.cost_usd + result["cost_usd"],
        total_latency_seconds=metrics.total_latency_seconds + latency,
        max_rate_limit_utilisation=_higher(
            metrics.max_rate_limit_utilisation, result["rate_limit_utilisation"]
        ),
        last_input_tokens=result["input_tokens"],
    )


def _higher(current: float | None, observed: float | None) -> float | None:
    """Return the greater of two optional utilisations, ignoring a missing one."""
    if observed is None:
        return current
    if current is None:
        return observed
    return max(current, observed)
