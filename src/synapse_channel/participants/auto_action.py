# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in automatic reactions to the advisor's per-round signals
"""React to the advisor's per-round signals with opt-in automatic actions.

The advisor (:func:`~synapse_channel.participants.session_advisor.assess_session`) is deliberately
**advice, not action** — it reports what an orchestrator *might* consider doing and never acts. This
module is the separate, opt-in step its docstring anticipates: a thin reactor that turns a chosen
subset of those signals into automatic actions (compact a filling context, write a log, hand the
run over) by invoking caller-supplied handlers.

Every axis is opt-in, so the default is to do nothing. An action fires for a round only when (1) the
signal is present in that round's advice, (2) the action is armed in the :class:`AutoActionPolicy`,
and (3) a handler for it was supplied. The advisor's verdict is never changed — this only acts on
it, leaving the advisor itself evidence-not-gate. The concrete handlers (what "compact" or "log"
actually does) are the operator's, injected the same way the orchestration loop takes its ``post``
sink, so this module stays free of any harness-specific side effect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from synapse_channel.participants.session_advisor import (
    Recommendation,
    SessionAdvice,
    SessionSignal,
)
from synapse_channel.participants.session_telemetry import SessionMetrics


class AutoAction(Enum):
    """An automatic operational action the reactor can take. The value is a stable lowercase tag."""

    COMPACT = "compact"
    LOG = "log"
    HANDOVER = "handover"


_SIGNAL_ACTION: dict[SessionSignal, AutoAction] = {
    SessionSignal.COMPACT_SOON: AutoAction.COMPACT,
    SessionSignal.LOG_NOW: AutoAction.LOG,
    SessionSignal.HIGH_ERROR_RATE: AutoAction.HANDOVER,
}
"""Fixed mapping from an advisory signal to the action it triggers.

Only the signals with a natural operational response map to an action. ``over-budget`` already halts
the orchestration loop, and ``approaching-rate-limit`` is already handled by the router steering the
next route away, so neither maps to an automatic action here.
"""


@dataclass(frozen=True)
class AutoActionPolicy:
    """Which actions are armed to fire automatically. The default arms nothing.

    Attributes
    ----------
    armed : frozenset[AutoAction]
        The actions allowed to fire. An action whose signal appears in a round's advice still does
        nothing unless it is in this set, so an orchestrator opts into exactly the reactions it
        wants.
    """

    armed: frozenset[AutoAction] = field(default_factory=frozenset)

    @classmethod
    def all_on(cls) -> AutoActionPolicy:
        """Return a policy that arms every action."""
        return cls(armed=frozenset(AutoAction))


@dataclass(frozen=True)
class AutoActionContext:
    """What a fired action handler is told about the round that triggered it.

    Attributes
    ----------
    session_id : str
        The deliberation's correlation id (the orchestration loop's ``topic_id``).
    round_index : int
        Zero-based index of the round whose advice triggered the action.
    recommendation : Recommendation
        The advisory signal and its reason that triggered this action.
    action : AutoAction
        The action being carried out.
    metrics : SessionMetrics
        The running session metrics after the triggering round.
    advice : SessionAdvice
        The full advice the triggering round raised.
    """

    session_id: str
    round_index: int
    recommendation: Recommendation
    action: AutoAction
    metrics: SessionMetrics
    advice: SessionAdvice


ActionHandler = Callable[[AutoActionContext], Awaitable[None]]
"""Coroutine that carries out one fired action; supplied by the operator, awaited when it fires."""


@dataclass(frozen=True)
class AutoActionDispatch:
    """An armed policy paired with the handlers that carry out each action.

    Bundling the two means the orchestration loop takes a single opt-in argument: ``None`` reacts to
    nothing, a dispatch reacts to exactly its armed-and-handled actions.

    Attributes
    ----------
    policy : AutoActionPolicy
        Which actions are armed to fire.
    handlers : Mapping[AutoAction, ActionHandler]
        The coroutine that carries out each action; an armed action with no handler still does
        nothing.
    """

    policy: AutoActionPolicy
    handlers: Mapping[AutoAction, ActionHandler]


async def react_to_advice(
    advice: SessionAdvice,
    dispatch: AutoActionDispatch,
    *,
    session_id: str,
    round_index: int,
    metrics: SessionMetrics,
) -> tuple[AutoAction, ...]:
    """Fire the armed, handled actions for the signals a round raised, in advisory order.

    An action fires only when its signal is present in ``advice``, the action is armed in the
    dispatch's policy, and a handler for it was supplied — opt-in on every axis. The advisor's
    verdict is unchanged; this only acts on it.

    Parameters
    ----------
    advice : SessionAdvice
        The advice the round raised.
    dispatch : AutoActionDispatch
        The armed policy and the handlers that carry out each action.
    session_id : str
        The deliberation's correlation id, passed to each handler.
    round_index : int
        Zero-based index of the round, passed to each handler.
    metrics : SessionMetrics
        The running metrics after the round, passed to each handler.

    Returns
    -------
    tuple[AutoAction, ...]
        The actions fired, in the advisor's recommendation order.
    """
    fired: list[AutoAction] = []
    for recommendation in advice.recommendations:
        action = _SIGNAL_ACTION.get(recommendation.signal)
        if action is None:
            continue
        if action not in dispatch.policy.armed:
            continue
        handler = dispatch.handlers.get(action)
        if handler is None:
            continue
        await handler(
            AutoActionContext(
                session_id=session_id,
                round_index=round_index,
                recommendation=recommendation,
                action=action,
                metrics=metrics,
                advice=advice,
            )
        )
        fired.append(action)
    return tuple(fired)
