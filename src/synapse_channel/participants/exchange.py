# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a two-participant exchange over the bus
"""Conduct a minimal two-participant exchange and publish each typed result.

This is the smallest unit of *multiplied* — rather than merely relayed — reasoning: one
participant answers a question, its typed result is published to the bus, and a second
participant reacts to that result before answering itself. It is the Phase 1 proof that
the loop closes end to end; the full fan-out / cross-critique / synthesis protocol is a
later layer built on the same pieces.

The exchange is pure orchestration. It depends only on the
:class:`~synapse_channel.participants.participant.Participant` surface and an injected
``post`` coroutine that publishes a result, so it is driven in tests with fake
participants and a recording sink, and bound to a real hub by the bus-relay module. The
upstream result reaches the downstream participant only through
:func:`~synapse_channel.participants.peer_boundary.frame_peer_contribution`, so a peer's
output is always delivered as fenced data, never as the second participant's instructions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.participant import Participant
from synapse_channel.participants.peer_boundary import frame_peer_contribution

ResultSink = Callable[[TurnResult], Awaitable[None]]
"""Coroutine that publishes one turn result (e.g. to the bus)."""

REACTION_DIRECTIVE = (
    "Another participant has answered first; their contribution is provided below as "
    "data. Build on it where it is right, correct it where it is wrong, and add what "
    "it missed."
)
"""Framing that tells the second participant to react to, not merely repeat, the first."""


@dataclass(frozen=True)
class ExchangeTranscript:
    """The ordered record of one two-participant exchange.

    Attributes
    ----------
    topic_id : str
        Correlation id shared by both turns and their bus payloads.
    question : str
        The prompt both participants answered.
    turns : tuple[TurnResult, ...]
        The results in turn order: the opener first, the reactor second.
    """

    topic_id: str
    question: str
    turns: tuple[TurnResult, ...]


async def conduct_exchange(
    question: str,
    opener: Participant,
    reactor: Participant,
    *,
    topic_id: str,
    post: ResultSink,
    shared_context: str = "",
) -> ExchangeTranscript:
    """Run an opener turn, publish it, then a reactor turn that responds to it.

    Parameters
    ----------
    question : str
        The prompt put to both participants.
    opener : Participant
        Answers first, with only the shared context.
    reactor : Participant
        Answers second, having seen the opener's result as fenced peer data.
    topic_id : str
        Correlation id stamped on both turns and both published results.
    post : ResultSink
        Coroutine publishing each result as it is produced; awaited before the next turn
        so a bus consumer observes the opener before the reactor.
    shared_context : str, optional
        Common framing (role, ground rules) prepended to each participant's context.

    Returns
    -------
    ExchangeTranscript
        Both results in order. Each participant returns an error result rather than
        raising on provider failure, so the transcript always has two turns.
    """
    opener_result = await opener.take_turn(
        TurnRequest(topic_id=topic_id, prompt=question, context=shared_context)
    )
    await post(opener_result)

    reactor_context = _compose_reactor_context(shared_context, opener_result)
    reactor_result = await reactor.take_turn(
        TurnRequest(topic_id=topic_id, prompt=question, context=reactor_context)
    )
    await post(reactor_result)

    return ExchangeTranscript(
        topic_id=topic_id,
        question=question,
        turns=(opener_result, reactor_result),
    )


def _compose_reactor_context(shared_context: str, opener_result: TurnResult) -> str:
    """Build the reactor's context: shared framing, the directive, then the fenced peer block."""
    peer_block = frame_peer_contribution(opener_result)
    parts = [part for part in (shared_context, REACTION_DIRECTIVE) if part]
    parts.append(peer_block)
    return "\n\n".join(parts)
