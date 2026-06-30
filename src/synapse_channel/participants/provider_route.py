# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — choose which provider and model should answer a task
"""Choose the provider and model best suited to a task.

Where :func:`~synapse_channel.participants.channel_select.select_channel` answers *how* to drive
one provider, this answers *which* provider to drive. Given a task profile and a set of candidate
models, :func:`select_provider` keeps only the candidates that can actually be driven (a channel is
available) and that carry every capability the task requires, then ranks the survivors and returns
the best with the channel to reach it and the cost it was ranked on.

The ranking is deterministic and explainable, in priority order:

1. **Headroom** — a candidate closer to its rate limit is less preferred, so the captured
   rate-limit signal steers load away from a provider about to throttle (a candidate at or over its
   limit is dropped entirely).
2. **Cost** — the cheaper estimated turn wins; a local model with no price table is free and so
   ranks cheapest, which is usually the right default.
3. **Channel robustness** — ties break toward the more robust channel (``MCP`` over ``API`` over
   ``HEADLESS`` over ``PTY``).

The function is pure over small descriptors with an injected ``PATH`` resolver, so a routing
decision is deterministic in tests; it selects but never constructs a participant, leaving that to
the caller. When nothing is eligible it returns ``None`` so the caller can report the task as
unroutable rather than guess.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import (
    PathResolver,
    ProviderCapabilities,
    select_channel,
)
from synapse_channel.participants.participant import ParticipantChannel

_CHANNEL_RANK = {channel: rank for rank, channel in enumerate(ParticipantChannel)}
"""Robustness rank per channel (lower is better), in the enum's declared order."""


@dataclass(frozen=True)
class ModelCandidate:
    """One provider/model the router may choose.

    Attributes
    ----------
    name : str
        Identifier of this option (e.g. a participant identity or a label).
    model : str
        The model id this candidate would run.
    capabilities : ProviderCapabilities
        Which channels can reach this provider, used to decide whether it is drivable at all.
    tags : frozenset[str]
        Capability tags the model offers (e.g. ``"code"``, ``"vision"``, ``"long-context"``,
        ``"local"``); a task's required tags must be a subset of these.
    price : ModelPrice or None
        Per-1k token price for cost ranking; ``None`` means free/unknown (ranked as free).
    rate_limit_utilisation : float or None
        The candidate's last known rate-limit fraction in ``[0, 1]``; ``None`` means full headroom.
        A value at or above ``1.0`` drops the candidate.
    """

    name: str
    model: str
    capabilities: ProviderCapabilities
    tags: frozenset[str] = field(default_factory=frozenset)
    price: ModelPrice | None = None
    rate_limit_utilisation: float | None = None


@dataclass(frozen=True)
class TaskProfile:
    """What a task needs, used to filter and cost candidates.

    Attributes
    ----------
    required_tags : frozenset[str]
        Capability tags a candidate must offer to be eligible; empty means any candidate qualifies.
    estimated_input_tokens : int
        Expected prompt tokens, used with a candidate's price to estimate cost.
    estimated_output_tokens : int
        Expected completion tokens, used with a candidate's price to estimate cost.
    """

    required_tags: frozenset[str] = field(default_factory=frozenset)
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0


@dataclass(frozen=True)
class RoutingChoice:
    """The router's pick for a task.

    Attributes
    ----------
    candidate : ModelCandidate
        The chosen provider/model.
    channel : ParticipantChannel
        The channel selected to drive it.
    estimated_cost : float
        The cost the candidate was ranked on, in the price table's currency unit.
    reason : str
        A short, human-readable explanation of why this candidate won.
    """

    candidate: ModelCandidate
    channel: ParticipantChannel
    estimated_cost: float
    reason: str


def _estimated_cost(candidate: ModelCandidate, task: TaskProfile) -> float:
    """Return the estimated cost for ``candidate`` on ``task`` (0.0 when unpriced)."""
    if candidate.price is None:
        return 0.0
    return candidate.price.estimate(task.estimated_input_tokens, task.estimated_output_tokens)


def select_provider(
    task: TaskProfile,
    candidates: list[ModelCandidate],
    *,
    which: PathResolver = shutil.which,
) -> RoutingChoice | None:
    """Return the best drivable, capable candidate for ``task``, or ``None``.

    A candidate is eligible when a channel can reach it
    (:func:`~synapse_channel.participants.channel_select.select_channel`), it offers every tag the
    task requires, and it is not at or over its rate limit. Eligible candidates are ranked by
    headroom, then estimated cost, then channel robustness; the first wins.

    Parameters
    ----------
    task : TaskProfile
        The task's required capabilities and expected token sizes.
    candidates : list[ModelCandidate]
        The provider/model options to choose among.
    which : PathResolver, optional
        Resolver passed through to channel selection; injected in tests for determinism.

    Returns
    -------
    RoutingChoice or None
        The selected candidate with its channel and ranked cost, or ``None`` when none qualify.
    """
    eligible: list[tuple[ModelCandidate, ParticipantChannel, float]] = []
    for candidate in candidates:
        channel = select_channel(candidate.capabilities, which=which)
        if channel is None:
            continue
        if not task.required_tags <= candidate.tags:
            continue
        utilisation = candidate.rate_limit_utilisation
        if utilisation is not None and utilisation >= 1.0:
            continue
        eligible.append((candidate, channel, _estimated_cost(candidate, task)))
    if not eligible:
        return None

    def rank(entry: tuple[ModelCandidate, ParticipantChannel, float]) -> tuple[float, float, int]:
        candidate, channel, cost = entry
        headroom = candidate.rate_limit_utilisation or 0.0
        return headroom, cost, _CHANNEL_RANK[channel]

    candidate, channel, cost = min(eligible, key=rank)
    reason = (
        f"selected {candidate.name!r} (model {candidate.model!r}) via {channel.value} "
        f"from {len(eligible)} eligible: est_cost={cost:.4f}, "
        f"utilisation={candidate.rate_limit_utilisation}"
    )
    return RoutingChoice(candidate=candidate, channel=channel, estimated_cost=cost, reason=reason)
