# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional memory decorator for any Participant
"""Decorate a Participant with bounded, fail-visible pre-turn memory recall."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from synapse_channel.participants.envelope import TurnRequest, TurnResult
from synapse_channel.participants.memory_boundary import (
    render_memory_context,
    render_memory_unavailable,
)
from synapse_channel.participants.memory_contract import (
    MemoryPolicy,
    MemoryRecall,
    MemoryRecallResult,
)
from synapse_channel.participants.participant import (
    Participant,
    ParticipantChannel,
    ParticipantHealth,
)


@dataclass(frozen=True)
class MemoryAugmentedParticipant:
    """Recall fenced context before delegating an otherwise unchanged turn."""

    participant: Participant
    recall: MemoryRecall
    policy: MemoryPolicy = field(default_factory=MemoryPolicy)
    source_label: str = "REMANENTIA"

    def __post_init__(self) -> None:
        """Require a visible service label for unavailable markers."""
        if not isinstance(self.source_label, str) or not self.source_label.strip():
            raise ValueError("source_label must be a non-empty string")

    @property
    def identity(self) -> str:
        """Delegate the wrapped participant's bus identity."""
        return self.participant.identity

    @property
    def channel(self) -> ParticipantChannel:
        """Delegate the wrapped participant's provider channel."""
        return self.participant.channel

    def health(self) -> ParticipantHealth:
        """Delegate readiness without consulting the memory service."""
        return self.participant.health()

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Recall under a hard timeout, fence the result, and delegate the turn."""
        try:
            result = await asyncio.wait_for(
                self.recall.recall(request.prompt, top_k=self.policy.top_k),
                timeout=self.policy.timeout_seconds,
            )
            if not isinstance(result, MemoryRecallResult) or result.query != request.prompt:
                raise ValueError("memory result did not match the exact turn prompt")
            memory_context = render_memory_context(
                result,
                max_hits=self.policy.top_k,
                max_chars=self.policy.max_chars,
            )
        except Exception:
            memory_context = render_memory_unavailable(
                source=self.source_label,
                max_chars=self.policy.max_chars,
            )

        context = f"{request.context}\n\n{memory_context}" if request.context else memory_context
        return await self.participant.take_turn(replace(request, context=context))
