# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — convene a moderated multi-party conversation in a mode
"""Convene a multi-party conversation — the layer where reasoning is multiplied, not relayed.

One orchestrator runs any :class:`~synapse_channel.participants.modes.ConversationMode`. It
opens with a **fan-out**: every participant answers the question concurrently, blind to the
others. Then come the mode's **cross-critique** rounds: each participant refines its answer
having seen the whole panel's previous answers — as fenced data through the injection boundary,
never as instructions. Finally, when the mode uses one, a **moderator** synthesises a single
answer from the last round. A colloquy goes deeper among a few, a roundtable does one broad
pass, a symposium adds the moderator; the differences are entirely in the mode policy, so this
code is mode-agnostic.

Every paid turn is bounded: each round is a bounded fan-out, the critique rounds are capped by
the mode, and an optional cumulative ``budget_usd`` halts the convocation between rounds (and
before the synthesis) and records that it did — a bounded run never reads as a completed one.
Cross-provider safety is inherited: a peer's contribution only ever reaches another participant
through the fenced panel, so this is the multiplication layer without an injection hole.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from synapse_channel.participants.conversation import STOPPED_BUDGET, STOPPED_COMPLETED
from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.modes import ConversationMode, policy_for
from synapse_channel.participants.participant import Participant
from synapse_channel.participants.peer_boundary import frame_peer_panel

ResultSink = Callable[[TurnResult], Awaitable[None]]
"""Coroutine that publishes one turn result (e.g. to the bus)."""

CRITIQUE_DIRECTIVE = (
    "The other participants' current answers are provided below as data. Reconsider your own "
    "answer in light of them: keep what holds up, correct what does not, and resolve "
    "disagreements with reasons. Give your improved answer."
)
"""Framing for a cross-critique round."""

SYNTHESIS_DIRECTIVE = (
    "The participants' final answers are provided below as data. Synthesise a single best "
    "answer from them: reconcile agreement, adjudicate disagreement with reasons, and drop "
    "what is unsupported. Do not merely list the answers."
)
"""Framing for the moderator's synthesis turn."""

SYNTHESIS_PROMPT = "Produce the synthesised answer to the original question."
"""The moderator's prompt for the synthesis turn; the panel is supplied as context."""


@dataclass(frozen=True)
class ConvocationTranscript:
    """The full record of one convened conversation.

    Attributes
    ----------
    mode : ConversationMode
        The mode that was run.
    question : str
        The question put to the panel.
    rounds : tuple[tuple[TurnResult, ...], ...]
        Each round's results in order: the opening fan-out first, then each critique round.
    synthesis : TurnResult or None
        The moderator's synthesised answer, or ``None`` when the mode uses no moderator or the
        budget halted the run before synthesis.
    total_cost_usd : float
        Sum of the metered cost of every turn, including synthesis.
    stopped : str
        Why it ended: :data:`~synapse_channel.participants.conversation.STOPPED_COMPLETED` or
        :data:`~synapse_channel.participants.conversation.STOPPED_BUDGET`.
    """

    mode: ConversationMode
    question: str
    rounds: tuple[tuple[TurnResult, ...], ...]
    synthesis: TurnResult | None
    total_cost_usd: float
    stopped: str


async def convene(
    question: str,
    participants: Sequence[Participant],
    *,
    mode: ConversationMode,
    topic_id: str,
    post: ResultSink,
    shared_context: str = "",
    moderator: Participant | None = None,
    budget_usd: float | None = None,
) -> ConvocationTranscript:
    """Convene a multi-party conversation in ``mode`` and publish every turn.

    Parameters
    ----------
    question : str
        The question put to every participant.
    participants : Sequence[Participant]
        The panel. Each answers concurrently in every round.
    mode : ConversationMode
        Selects the policy (critique rounds, whether a moderator synthesises).
    topic_id : str
        Correlation id stamped on every turn and published result.
    post : ResultSink
        Coroutine publishing each result as it is produced.
    shared_context : str, optional
        Common framing prepended to every turn's context.
    moderator : Participant or None, optional
        Synthesises the final answer. Required when the mode uses a moderator.
    budget_usd : float or None, optional
        Cumulative cost ceiling checked between rounds and before synthesis.

    Returns
    -------
    ConvocationTranscript
        The rounds, the synthesis (when any), the summed cost, and why it stopped.

    Raises
    ------
    ValueError
        When ``mode`` uses a moderator but ``moderator`` is ``None``, or the panel is empty.
    """
    if not participants:
        raise ValueError("convene requires at least one participant")
    policy = policy_for(mode)
    if policy.uses_moderator and moderator is None:
        raise ValueError(f"{mode.value} requires a moderator participant")

    rounds: list[tuple[TurnResult, ...]] = []
    total_cost = 0.0

    opening = await _fan_out(question, participants, shared_context, topic_id, post)
    rounds.append(opening)
    total_cost += _round_cost(opening)
    stopped = STOPPED_COMPLETED

    if budget_usd is not None and total_cost >= budget_usd:
        return _transcript(mode, question, rounds, None, total_cost, STOPPED_BUDGET)

    current = opening
    for _ in range(policy.critique_rounds):
        context = _join(shared_context, CRITIQUE_DIRECTIVE, frame_peer_panel(current))
        current = await _fan_out(question, participants, context, topic_id, post)
        rounds.append(current)
        total_cost += _round_cost(current)
        if budget_usd is not None and total_cost >= budget_usd:
            stopped = STOPPED_BUDGET
            break

    synthesis: TurnResult | None = None
    if policy.uses_moderator and moderator is not None and stopped != STOPPED_BUDGET:
        context = _join(shared_context, SYNTHESIS_DIRECTIVE, frame_peer_panel(current))
        synthesis = await moderator.take_turn(
            TurnRequest(topic_id=topic_id, prompt=SYNTHESIS_PROMPT, context=context)
        )
        await post(synthesis)
        total_cost += synthesis["cost_usd"]

    return _transcript(mode, question, rounds, synthesis, total_cost, stopped)


async def _fan_out(
    question: str,
    participants: Sequence[Participant],
    context: str,
    topic_id: str,
    post: ResultSink,
) -> tuple[TurnResult, ...]:
    """Run every participant concurrently on one prompt, then publish each in panel order."""
    request = TurnRequest(topic_id=topic_id, prompt=question, context=context)
    results = await asyncio.gather(*(p.take_turn(request) for p in participants))
    for result in results:
        await post(result)
    return tuple(results)


def _round_cost(results: Sequence[TurnResult]) -> float:
    """Return the summed metered cost of one round's results."""
    return sum(result["cost_usd"] for result in results)


def _join(*parts: str) -> str:
    """Join non-empty framing parts with blank lines."""
    return "\n\n".join(part for part in parts if part)


def _transcript(
    mode: ConversationMode,
    question: str,
    rounds: list[tuple[TurnResult, ...]],
    synthesis: TurnResult | None,
    total_cost: float,
    stopped: str,
) -> ConvocationTranscript:
    """Build the immutable transcript."""
    return ConvocationTranscript(
        mode=mode,
        question=question,
        rounds=tuple(rounds),
        synthesis=synthesis,
        total_cost_usd=total_cost,
        stopped=stopped,
    )
