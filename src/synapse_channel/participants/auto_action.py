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
from typing import Any

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


_UNMAPPED_SIGNAL_REASON: dict[SessionSignal, str] = {
    SessionSignal.OVER_BUDGET: "already halts the orchestration loop",
    SessionSignal.APPROACHING_RATE_LIMIT: "already steered away by the router",
}
"""Why each advisory signal outside :data:`_SIGNAL_ACTION` maps to no automatic action."""


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


@dataclass(frozen=True)
class AutoActionDescription:
    """One action in the reactor model: the signal it reacts to and whether it is armed.

    Attributes
    ----------
    action : AutoAction
        The automatic action.
    signal : SessionSignal
        The advisory signal that triggers it.
    armed : bool
        Whether the introspected policy arms this action. An armed action still fires at runtime
        only when its signal is raised in a round and a handler for it was supplied.
    """

    action: AutoAction
    signal: SessionSignal
    armed: bool


@dataclass(frozen=True)
class UnmappedSignal:
    """An advisory signal that deliberately triggers no automatic action, and why.

    Attributes
    ----------
    signal : SessionSignal
        The advisory signal.
    reason : str
        Why it maps to no action here — it is handled on another path.
    """

    signal: SessionSignal
    reason: str


@dataclass(frozen=True)
class AutoActionReport:
    """Read-only projection of the reactor's action model for a chosen armed set.

    Attributes
    ----------
    descriptions : tuple[AutoActionDescription, ...]
        Every mapped action, in the fixed signal-to-action order, marked armed or not.
    unmapped_signals : tuple[UnmappedSignal, ...]
        The advisory signals that map to no action, each with the reason it is handled elsewhere.
    """

    descriptions: tuple[AutoActionDescription, ...]
    unmapped_signals: tuple[UnmappedSignal, ...]

    @property
    def armed(self) -> tuple[AutoAction, ...]:
        """Return the actions this report marks armed, in signal-to-action order."""
        return tuple(item.action for item in self.descriptions if item.armed)


def describe_auto_actions(armed: frozenset[AutoAction] = frozenset()) -> AutoActionReport:
    """Project the fixed signal-to-action model, marking which of ``armed`` would fire.

    Read-only introspection for discoverability: it constructs no live dispatch and fires nothing.
    Arming is necessary but not sufficient — an action fires at runtime only when its signal is
    raised in a round *and* a handler was supplied to :func:`react_to_advice`.

    Parameters
    ----------
    armed : frozenset[AutoAction], optional
        The actions to mark as armed. Defaults to the empty set (arm nothing), matching the default
        :class:`AutoActionPolicy`.

    Returns
    -------
    AutoActionReport
        The mapped actions with their armed state and the deliberately unmapped signals.
    """
    descriptions = tuple(
        AutoActionDescription(action=action, signal=signal, armed=action in armed)
        for signal, action in _SIGNAL_ACTION.items()
    )
    unmapped = tuple(
        UnmappedSignal(
            signal=signal,
            reason=_UNMAPPED_SIGNAL_REASON.get(signal, "no automatic action"),
        )
        for signal in SessionSignal
        if signal not in _SIGNAL_ACTION
    )
    return AutoActionReport(descriptions=descriptions, unmapped_signals=unmapped)


def auto_action_report_to_json(report: AutoActionReport) -> dict[str, Any]:
    """Return a JSON-ready mapping for an auto-action introspection report."""
    return {
        "actions": [
            {"action": item.action.value, "signal": item.signal.value, "armed": item.armed}
            for item in report.descriptions
        ],
        "unmapped_signals": [
            {"signal": item.signal.value, "reason": item.reason} for item in report.unmapped_signals
        ],
    }


def render_auto_action_report(report: AutoActionReport) -> str:
    """Render a human-readable auto-action introspection report."""
    lines = ["Auto-action reactor — advisory signals mapped to opt-in automatic actions:"]
    for item in report.descriptions:
        state = "armed" if item.armed else "available"
        lines.append(f"  {item.action.value:<9} <- {item.signal.value:<23} ({state})")
    lines.append("")
    lines.append("Advisory signals with no automatic action (handled on another path):")
    for unmapped in report.unmapped_signals:
        lines.append(f"  {unmapped.signal.value:<23} {unmapped.reason}")
    lines.append("")
    lines.append(
        "An armed action fires only when its signal is raised in a round and a handler was "
        "supplied to react_to_advice; arming alone does not act."
    )
    return "\n".join(lines)
