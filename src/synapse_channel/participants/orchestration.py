# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a routed, telemetered multi-round deliberation loop
"""Run a multi-round deliberation that routes each turn and reacts to its own telemetry.

This is where the Participant Fabric's separate Phase 5 pieces — the provider router, the running
session telemetry, the operational advisor, and the durable session-metric bridge — come together
in one live loop. It generalises
:func:`~synapse_channel.participants.conversation.conduct_conversation`: instead of cycling a fixed
list of participants in order, each round asks the **router**
(:func:`~synapse_channel.participants.provider_route.select_provider`) which provider should answer
*now*, drives that participant, folds the result into the running
:class:`~synapse_channel.participants.session_telemetry.SessionMetrics`, and asks the **advisor**
(:func:`~synapse_channel.participants.session_advisor.assess_session`) what the telemetry implies.

The loop closes on itself: a turn's reported rate-limit utilisation is fed back into that
provider's candidate before the next routing decision, so load steers away from a provider nearing
its limit without any external signal. The advisor's verdict stays **advice, not action** —
recorded each round for a caller to read — with one bounding exception that mirrors the budget guard
in :func:`conduct_conversation`: an ``over-budget`` signal halts the run, so a bounded deliberation
never reads as a completed one. When a poster is supplied the loop also persists a durable
``session_metric`` snapshot each round (opt-in; the first live caller of
:func:`~synapse_channel.participants.session_metric_emit.emit_session_metric`), leaving the hub core
a no-telemetry substrate throughout.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace

from synapse_channel.participants.auto_action import (
    AutoAction,
    AutoActionDispatch,
    react_to_advice,
)
from synapse_channel.participants.channel_select import PathResolver
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.exchange import REACTION_DIRECTIVE
from synapse_channel.participants.participant import Participant
from synapse_channel.participants.peer_boundary import frame_peer_contribution
from synapse_channel.participants.provider_route import (
    ModelCandidate,
    RoutingChoice,
    TaskProfile,
    select_provider,
)
from synapse_channel.participants.session_advisor import (
    AdvisorThresholds,
    SessionAdvice,
    SessionSignal,
    assess_session,
)
from synapse_channel.participants.session_metric_emit import (
    ProgressPoster,
    emit_session_metric,
)
from synapse_channel.participants.session_telemetry import SessionMetrics, accumulate

ResultSink = Callable[[TurnResult], Awaitable[None]]
"""Coroutine that publishes one turn result (e.g. to the bus)."""

Clock = Callable[[], float]
"""Monotonic clock returning seconds, injected so per-turn latency is deterministic in tests."""

STOPPED_COMPLETED = "completed"
"""All requested rounds ran."""

STOPPED_BUDGET = "budget"
"""The advisor raised ``over-budget`` and the deliberation halted early."""

STOPPED_UNROUTABLE = "unroutable"
"""No candidate could be routed for a round (all ineligible or at their rate limit)."""

STOPPED_EMPTY = "empty"
"""Nothing ran — an empty roster, or a non-positive round count."""


@dataclass(frozen=True)
class OrchestrationSeat:
    """One routable participant: the driver paired with the candidate the router scores.

    Attributes
    ----------
    participant : Participant
        The provider session driven when this seat is chosen.
    candidate : ModelCandidate
        The router-facing descriptor (channels, tags, price, last rate-limit utilisation). Its
        ``name`` must be unique across the roster, as it links a routing choice back to its driver.
    """

    participant: Participant
    candidate: ModelCandidate


@dataclass(frozen=True)
class OrchestrationRound:
    """The record of one routed, telemetered turn.

    Attributes
    ----------
    index : int
        Zero-based round number.
    choice : RoutingChoice
        The router's pick for this round, with the channel and ranked cost.
    result : TurnResult
        The driven turn's structured outcome.
    latency_seconds : float
        Wall-clock time the turn took, as measured by the injected clock.
    metrics : SessionMetrics
        The running session metrics *after* this turn was folded in.
    advice : SessionAdvice
        The advisory signals the telemetry raised after this turn.
    fired_actions : tuple[AutoAction, ...]
        The automatic actions taken in response to this round's advice; empty unless an opt-in
        :class:`~synapse_channel.participants.auto_action.AutoActionDispatch` was supplied.
    """

    index: int
    choice: RoutingChoice
    result: TurnResult
    latency_seconds: float
    metrics: SessionMetrics
    advice: SessionAdvice
    fired_actions: tuple[AutoAction, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OrchestrationTranscript:
    """The ordered record of a routed deliberation.

    Attributes
    ----------
    topic_id : str
        Correlation id shared by every turn and published payload.
    question : str
        The prompt put to each turn.
    rounds : tuple[OrchestrationRound, ...]
        Each routed turn in order, with its telemetry and advice.
    metrics : SessionMetrics
        The final running metrics across the whole deliberation.
    stopped : str
        Why the deliberation ended: :data:`STOPPED_COMPLETED`, :data:`STOPPED_BUDGET`,
        :data:`STOPPED_UNROUTABLE`, or :data:`STOPPED_EMPTY`.
    """

    topic_id: str
    question: str
    rounds: tuple[OrchestrationRound, ...]
    metrics: SessionMetrics
    stopped: str


async def orchestrate_session(
    question: str,
    roster: Sequence[OrchestrationSeat],
    *,
    rounds: int,
    topic_id: str,
    task: TaskProfile,
    thresholds: AdvisorThresholds,
    post: ResultSink | None = None,
    shared_context: str = "",
    clock: Clock = time.monotonic,
    which: PathResolver | None = None,
    post_progress: ProgressPoster | None = None,
    auto_action: AutoActionDispatch | None = None,
) -> OrchestrationTranscript:
    """Run up to ``rounds`` routed turns over ``roster``, reacting to telemetry each round.

    Each round routes ``task`` over the roster's candidates — refreshed with the rate-limit
    utilisation observed so far, so a provider nearing its limit is steered away from — drives the
    chosen participant on ``question`` (the previous turn framed in as peer data), folds the result
    into the running metrics, and assesses them. The advisor is advisory only, with one bounding
    exception: an ``over-budget`` signal halts the run.

    Parameters
    ----------
    question : str
        The prompt put to every turn.
    roster : Sequence[OrchestrationSeat]
        The routable participants; candidate names must be unique. An empty roster runs nothing.
    rounds : int
        Maximum number of turns to run. A non-positive value runs nothing.
    topic_id : str
        Correlation id stamped on every turn, published result, and durable snapshot.
    task : TaskProfile
        The task's required capabilities and expected token sizes, used to route every round.
    thresholds : AdvisorThresholds
        The advisor's thresholds; an ``over-budget`` signal also bounds the run.
    post : ResultSink or None, optional
        Coroutine publishing each result as it is produced; awaited before the next turn. ``None``
        publishes nothing.
    shared_context : str, optional
        Common framing prepended to every turn's context.
    clock : Clock, optional
        Monotonic clock used to measure per-turn latency; injected for deterministic tests.
    which : PathResolver or None, optional
        ``PATH`` resolver passed through to routing; ``None`` uses the router's default.
    post_progress : ProgressPoster or None, optional
        When supplied, a durable ``session_metric`` snapshot is emitted after each turn (opt-in;
        ``None`` emits nothing, keeping the run telemetry-free).
    auto_action : AutoActionDispatch or None, optional
        When supplied, each round's advice is reacted to with the dispatch's armed, handled actions
        (opt-in; ``None`` reacts to nothing, leaving the advisor purely advisory).

    Returns
    -------
    OrchestrationTranscript
        Every routed round with its telemetry and advice, the final metrics, and why it stopped.
    """
    if not roster or rounds <= 0:
        return OrchestrationTranscript(
            topic_id=topic_id,
            question=question,
            rounds=(),
            metrics=SessionMetrics(),
            stopped=STOPPED_EMPTY,
        )

    seat_by_name = {seat.candidate.name: seat for seat in roster}
    observed_utilisation: dict[str, float] = {}
    records: list[OrchestrationRound] = []
    metrics = SessionMetrics()
    previous: TurnResult | None = None
    stopped = STOPPED_COMPLETED

    for index in range(rounds):
        candidates = _route_candidates(roster, observed_utilisation)
        choice = (
            select_provider(task, candidates, which=which)
            if which is not None
            else select_provider(task, candidates)
        )
        if choice is None:
            stopped = STOPPED_UNROUTABLE
            break

        seat = seat_by_name[choice.candidate.name]
        context = _compose_round_context(shared_context, previous)
        request = TurnRequest(
            topic_id=topic_id,
            prompt=question,
            context=context,
            model=choice.candidate.model,
        )
        started = clock()
        result = await seat.participant.take_turn(request)
        latency = max(0.0, clock() - started)
        if post is not None:
            await post(result)

        utilisation = result["rate_limit_utilisation"]
        if utilisation is not None:
            observed_utilisation[choice.candidate.name] = utilisation
        metrics = accumulate(metrics, result, latency_seconds=latency)
        advice = assess_session(metrics, thresholds)
        if post_progress is not None:
            await emit_session_metric(metrics, post_progress=post_progress, session_id=topic_id)

        fired_actions: tuple[AutoAction, ...] = ()
        if auto_action is not None:
            fired_actions = await react_to_advice(
                advice,
                auto_action,
                session_id=topic_id,
                round_index=index,
                metrics=metrics,
            )

        records.append(
            OrchestrationRound(
                index=index,
                choice=choice,
                result=result,
                latency_seconds=latency,
                metrics=metrics,
                advice=advice,
                fired_actions=fired_actions,
            )
        )
        previous = result
        if SessionSignal.OVER_BUDGET in advice.signals:
            stopped = STOPPED_BUDGET
            break

    return OrchestrationTranscript(
        topic_id=topic_id,
        question=question,
        rounds=tuple(records),
        metrics=metrics,
        stopped=stopped,
    )


def _route_candidates(
    roster: Sequence[OrchestrationSeat], observed_utilisation: dict[str, float]
) -> list[ModelCandidate]:
    """Return the roster's candidates with each one's last observed utilisation folded in.

    A candidate keeps its declared utilisation until a turn reports one for it; from then on the
    observed value wins, so routing reacts to live rate-limit pressure.
    """
    candidates: list[ModelCandidate] = []
    for seat in roster:
        observed = observed_utilisation.get(seat.candidate.name)
        if observed is None:
            candidates.append(seat.candidate)
        else:
            candidates.append(replace(seat.candidate, rate_limit_utilisation=observed))
    return candidates


def _compose_round_context(shared_context: str, previous: TurnResult | None) -> str:
    """Build a round's context: shared framing, and the framed previous turn when there is one."""
    if previous is None:
        return shared_context
    peer_block = frame_peer_contribution(previous)
    parts = [part for part in (shared_context, REACTION_DIRECTIVE) if part]
    parts.append(peer_block)
    return "\n\n".join(parts)
