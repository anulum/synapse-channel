# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — choose which provider and model should answer a task
"""Route a task from explicit capability, price, quota, and data-policy evidence.

Current observations win over unknown or stale observations. Within an evidence
class, known utilisation, comparable estimated cost, and channel robustness
rank candidates. Unpriced use never appears as a zero-cost estimate. Advisory
routing can admit unknown observations under an explicit operator policy; a
bounded-cost task requires a current comparable quote independently of policy.
"""

from __future__ import annotations

import math
import shutil
import time
from dataclasses import dataclass, field
from typing import Literal

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import (
    PathResolver,
    ProviderCapabilities,
    select_channel,
)
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.provider_route_policy import (
    EvidenceStatus,
    PriceKind,
    RoutingPolicy,
    price_status,
    quota_status,
)

_CHANNEL_RANK = {channel: rank for rank, channel in enumerate(ParticipantChannel)}
"""Channel preference after comparable price and quota evidence."""

AccountStatus = Literal["active", "suspended", "expired"]
"""Account states that can affect dispatch eligibility."""


@dataclass(frozen=True)
class ModelCandidate:
    """One provider/model with its routing evidence.

    Parameters
    ----------
    name, model : str
        Candidate identity and model identifier.
    capabilities : ProviderCapabilities
        Channels that may drive the model.
    tags, data_classes : frozenset[str]
        Required task features and data classifications accepted by the provider.
    price : ModelPrice or None
        Quoted input/output cost per 1000 tokens. Absence means unknown unless
        ``price_kind=PriceKind.FREE`` is explicitly declared.
    price_kind : PriceKind or None
        Explicit free/priced/unknown classification; None infers priced from a
        quote and unknown from its absence for legacy callers.
    price_currency, price_revision, price_source : str
        Comparable currency and provenance of the quote.
    price_observed_at, price_valid_until : float or None
        UNIX observation and expiry times for the quote.
    rate_limit_utilisation : float or None
        Observed fraction of quota consumed; absence is unknown.
    quota_source, quota_observed_at, quota_valid_until : str, float or None
        Provenance and time limits of the quota observation.
    account_status : {"active", "suspended", "expired"}
        Account eligibility. A suspended or expired account cannot be routed.
    """

    name: str
    model: str
    capabilities: ProviderCapabilities
    tags: frozenset[str] = field(default_factory=frozenset)
    data_classes: frozenset[str] = field(default_factory=lambda: frozenset({"public"}))
    price: ModelPrice | None = None
    price_kind: PriceKind | None = None
    price_currency: str = "USD"
    price_revision: str = ""
    price_source: str = ""
    price_observed_at: float | None = None
    price_valid_until: float | None = None
    rate_limit_utilisation: float | None = None
    quota_source: str = ""
    quota_observed_at: float | None = None
    quota_valid_until: float | None = None
    account_status: AccountStatus = "active"

    @property
    def effective_price_kind(self) -> PriceKind:
        """Classify legacy quotes without treating a missing price as free."""
        if self.price_kind is not None:
            return self.price_kind
        return PriceKind.PRICED if self.price is not None else PriceKind.UNKNOWN


@dataclass(frozen=True)
class TaskProfile:
    """Capability, data, and spend requirements for one routed task.

    Parameters
    ----------
    required_tags : frozenset[str]
        Features the chosen candidate must expose.
    estimated_input_tokens, estimated_output_tokens : int
        Forecast token counts used for price ranking and a cost ceiling.
    data_classification : str
        Classification the candidate must explicitly accept.
    max_estimated_cost : float or None
        Hard upper bound on the routing estimate in ``currency``. This checks a
        quotation; it is not a provider-side spending reservation or billing cap.
    currency : str
        Currency of ``max_estimated_cost`` and comparable quotes.
    policy : RoutingPolicy
        Operator rules for unknown/stale observations in unbounded advisory use.
    """

    required_tags: frozenset[str] = field(default_factory=frozenset)
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    data_classification: str = "public"
    max_estimated_cost: float | None = None
    currency: str = "USD"
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)

    def __post_init__(self) -> None:
        """Reject invalid task estimates before evaluating candidates."""
        if self.estimated_input_tokens < 0 or self.estimated_output_tokens < 0:
            raise ValueError("estimated token counts must be nonnegative")
        if self.max_estimated_cost is not None and (
            not math.isfinite(self.max_estimated_cost) or self.max_estimated_cost < 0
        ):
            raise ValueError("max_estimated_cost must be finite and nonnegative")
        if not self.currency or not self.data_classification:
            raise ValueError("currency and data_classification must be nonempty")


@dataclass(frozen=True)
class RouteRejection:
    """Stable reason code for a candidate excluded before ranking."""

    candidate: str
    code: str


@dataclass(frozen=True)
class RoutingChoice:
    """The selected participant and the evidence used to rank it.

    ``estimated_cost`` is None when cost is unknown; it never encodes unknown
    as zero. Price and quota statuses remain visible to downstream callers.
    """

    candidate: ModelCandidate
    channel: ParticipantChannel
    estimated_cost: float | None
    price_status: EvidenceStatus
    quota_status: EvidenceStatus
    reason: str


@dataclass(frozen=True)
class RoutingDecision:
    """One selection, or no selection, plus exclusions with stable reason codes."""

    choice: RoutingChoice | None
    rejected: tuple[RouteRejection, ...]


def route_candidates(
    task: TaskProfile,
    candidates: list[ModelCandidate],
    *,
    which: PathResolver = shutil.which,
    now: float | None = None,
) -> RoutingDecision:
    """Evaluate each candidate and return the ranked choice with exclusions.

    Parameters
    ----------
    task : TaskProfile
        Feature, data, cost, and uncertainty policy for the turn.
    candidates : list[ModelCandidate]
        Provider observations to evaluate in their given order.
    which : PathResolver, optional
        Binary resolver for headless channels.
    now : float or None, optional
        UNIX time for observation expiry; defaults to current wall time.

    Returns
    -------
    RoutingDecision
        Chosen route, if any, and a reason code per excluded candidate.
    """
    observed_now = time.time() if now is None else now
    if not math.isfinite(observed_now) or observed_now < 0:
        raise ValueError("now must be finite and nonnegative")
    eligible: list[
        tuple[ModelCandidate, ParticipantChannel, float | None, EvidenceStatus, EvidenceStatus]
    ] = []
    rejected: list[RouteRejection] = []
    for candidate in candidates:
        channel = select_channel(candidate.capabilities, which=which)
        if channel is None:
            rejected.append(RouteRejection(candidate.name, "unreachable"))
            continue
        price_state = price_status(
            candidate.effective_price_kind,
            candidate.price,
            observed_at=candidate.price_observed_at,
            valid_until=candidate.price_valid_until,
            now=observed_now,
        )
        quota_state = quota_status(
            candidate.rate_limit_utilisation,
            observed_at=candidate.quota_observed_at,
            valid_until=candidate.quota_valid_until,
            now=observed_now,
        )
        code = _rejection_code(task, candidate, price_state, quota_state)
        if code is not None:
            rejected.append(RouteRejection(candidate.name, code))
            continue
        cost = _estimated_cost(candidate, task)
        eligible.append((candidate, channel, cost, price_state, quota_state))
    if not eligible:
        return RoutingDecision(choice=None, rejected=tuple(rejected))

    def rank(
        entry: tuple[
            ModelCandidate, ParticipantChannel, float | None, EvidenceStatus, EvidenceStatus
        ],
    ) -> tuple[int, float, int, float, int]:
        candidate, channel, cost, price_state, quota_state = entry
        quota_penalty = 0 if quota_state is EvidenceStatus.CURRENT else 1
        utilisation = candidate.rate_limit_utilisation or 0.0
        price_penalty = 0 if price_state is EvidenceStatus.CURRENT else 1
        return (
            quota_penalty,
            utilisation,
            price_penalty,
            cost if cost is not None else math.inf,
            _CHANNEL_RANK[channel],
        )

    candidate, channel, cost, price_state, quota_state = min(eligible, key=rank)
    cost_text = "unknown" if cost is None else f"{cost:.4f} {task.currency}"
    reason = (
        f"selected {candidate.name!r} (model {candidate.model!r}) via {channel.value} "
        f"from {len(eligible)} eligible: est_cost={cost_text}, "
        f"price={price_state.value}, quota={quota_state.value}, "
        f"utilisation={candidate.rate_limit_utilisation}"
    )
    return RoutingDecision(
        choice=RoutingChoice(candidate, channel, cost, price_state, quota_state, reason),
        rejected=tuple(rejected),
    )


def select_provider(
    task: TaskProfile,
    candidates: list[ModelCandidate],
    *,
    which: PathResolver = shutil.which,
) -> RoutingChoice | None:
    """Return the chosen route, or None; use :func:`route_candidates` for exclusions."""
    return route_candidates(task, candidates, which=which).choice


def _estimated_cost(candidate: ModelCandidate, task: TaskProfile) -> float | None:
    """Estimate comparable cost, preserving unknown as None."""
    if candidate.effective_price_kind is PriceKind.FREE:
        return 0.0
    if candidate.price is None:
        return None
    try:
        return candidate.price.estimate(task.estimated_input_tokens, task.estimated_output_tokens)
    except OverflowError:
        return math.inf


def _rejection_code(
    task: TaskProfile,
    candidate: ModelCandidate,
    price_state: EvidenceStatus,
    quota_state: EvidenceStatus,
) -> str | None:
    """Return the first stable exclusion reason in policy precedence order."""
    if not task.required_tags <= candidate.tags:
        return "missing_capability"
    if task.data_classification not in candidate.data_classes:
        return "data_policy"
    if candidate.account_status != "active":
        return "account_" + candidate.account_status
    if price_state is EvidenceStatus.INVALID:
        return "invalid_price"
    estimated = _estimated_cost(candidate, task)
    if estimated is not None and (not math.isfinite(estimated) or estimated < 0):
        return "invalid_price"
    if quota_state is EvidenceStatus.INVALID:
        return "invalid_quota"
    if candidate.rate_limit_utilisation is not None and candidate.rate_limit_utilisation >= 1:
        return "quota_exhausted"
    if (
        candidate.effective_price_kind is PriceKind.PRICED
        and candidate.price_currency != task.currency
    ):
        return "currency_mismatch"
    if task.max_estimated_cost is not None:
        if price_state is not EvidenceStatus.CURRENT:
            return "cost_unverified"
        if estimated is None or not math.isfinite(estimated):
            return "cost_unverified"
        if estimated > task.max_estimated_cost:
            return "cost_ceiling"
    if price_state is EvidenceStatus.UNKNOWN and task.policy.unknown_price == "refuse":
        return "unknown_price"
    if price_state is EvidenceStatus.STALE and task.policy.stale_price == "refuse":
        return "stale_price"
    if quota_state is EvidenceStatus.UNKNOWN and task.policy.unknown_quota == "refuse":
        return "unknown_quota"
    if quota_state is EvidenceStatus.STALE and task.policy.stale_quota == "refuse":
        return "stale_quota"
    return None
