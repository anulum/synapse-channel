# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a multi-round conversation across participants
"""Run a bounded multi-round conversation across one or more participants.

:func:`~synapse_channel.participants.exchange.conduct_exchange` is the two-turn case;
:func:`conduct_conversation` generalises it to ``rounds`` turns that cycle through a list of
participants. Each round after the first reacts to the immediately preceding turn's result —
framed as data through the injection boundary — while a participant's own earlier turns are
remembered through its provider session (wrap it in a
:class:`~synapse_channel.participants.continuity.ContinuitySeat` to keep that memory). The
result is a real multi-party deliberation with memory: A answers, B reacts to A, A resumes
and reacts to B, and so on.

Every paid turn is bounded twice over: a hard ``rounds`` cap, and an optional cumulative
``budget_usd`` ceiling that stops the conversation early. When the budget halts it, the
transcript says so explicitly (``stopped == "budget"``) — a bounded run never reads as a
completed one. This is the seed of the Phase 3 cost/loop guards; the full fan-out and
moderated synthesis layer builds on it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.exchange import REACTION_DIRECTIVE
from synapse_channel.participants.participant import Participant
from synapse_channel.participants.peer_boundary import frame_peer_contribution

ResultSink = Callable[[TurnResult], Awaitable[None]]
"""Coroutine that publishes one turn result (e.g. to the bus)."""

STOPPED_COMPLETED = "completed"
"""All requested rounds ran."""

STOPPED_BUDGET = "budget"
"""The cumulative cost reached ``budget_usd`` and the conversation halted early."""

STOPPED_EMPTY = "empty"
"""Nothing ran — no participants, or a non-positive round count."""


@dataclass(frozen=True)
class ConversationTranscript:
    """The ordered record of a multi-round conversation.

    Attributes
    ----------
    topic_id : str
        Correlation id shared by every turn and every published payload.
    question : str
        The prompt put to each turn.
    turns : tuple[TurnResult, ...]
        The results in round order.
    total_cost_usd : float
        Sum of the metered cost of every turn that ran.
    stopped : str
        Why the conversation ended: :data:`STOPPED_COMPLETED`, :data:`STOPPED_BUDGET`,
        or :data:`STOPPED_EMPTY`.
    """

    topic_id: str
    question: str
    turns: tuple[TurnResult, ...]
    total_cost_usd: float
    stopped: str


async def conduct_conversation(
    question: str,
    participants: Sequence[Participant],
    *,
    rounds: int,
    topic_id: str,
    post: ResultSink,
    shared_context: str = "",
    budget_usd: float | None = None,
) -> ConversationTranscript:
    """Run up to ``rounds`` turns cycling through ``participants``, publishing each.

    Parameters
    ----------
    question : str
        The prompt put to every turn.
    participants : Sequence[Participant]
        Cycled in order, one per round (``participants[r % len(participants)]``). Wrap each in
        a :class:`~synapse_channel.participants.continuity.ContinuitySeat` to give it memory
        across its turns.
    rounds : int
        Maximum number of turns to run. A non-positive value runs nothing.
    topic_id : str
        Correlation id stamped on every turn and published result.
    post : ResultSink
        Coroutine publishing each result as it is produced, awaited before the next turn so a
        bus consumer observes the turns in order.
    shared_context : str, optional
        Common framing prepended to every turn's context.
    budget_usd : float or None, optional
        Cumulative cost ceiling. When the running total reaches it after a turn, the
        conversation stops and the transcript records :data:`STOPPED_BUDGET`. ``None`` applies
        no budget (only the ``rounds`` cap bounds the run).

    Returns
    -------
    ConversationTranscript
        Every turn that ran, the summed cost, and why it stopped.
    """
    if not participants or rounds <= 0:
        return ConversationTranscript(
            topic_id=topic_id,
            question=question,
            turns=(),
            total_cost_usd=0.0,
            stopped=STOPPED_EMPTY,
        )

    turns: list[TurnResult] = []
    total_cost = 0.0
    previous: TurnResult | None = None
    stopped = STOPPED_COMPLETED

    for index in range(rounds):
        participant = participants[index % len(participants)]
        context = _compose_round_context(shared_context, previous)
        result = await participant.take_turn(
            TurnRequest(topic_id=topic_id, prompt=question, context=context)
        )
        await post(result)
        turns.append(result)
        total_cost += result["cost_usd"]
        previous = result
        if budget_usd is not None and total_cost >= budget_usd:
            stopped = STOPPED_BUDGET
            break

    return ConversationTranscript(
        topic_id=topic_id,
        question=question,
        turns=tuple(turns),
        total_cost_usd=total_cost,
        stopped=stopped,
    )


def _compose_round_context(shared_context: str, previous: TurnResult | None) -> str:
    """Build a round's context: shared framing, and the framed previous turn when there is one."""
    if previous is None:
        return shared_context
    peer_block = frame_peer_contribution(previous)
    parts = [part for part in (shared_context, REACTION_DIRECTIVE) if part]
    parts.append(peer_block)
    return "\n\n".join(parts)
