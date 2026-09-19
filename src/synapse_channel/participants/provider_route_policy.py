# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — evidence and operator policy for participant routing
"""Classify price and quota evidence before a provider can be ranked."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from synapse_channel.core.accounting import ModelPrice


class PriceKind(str, Enum):
    """Whether a candidate has a quoted price, verified free use, or no price."""

    PRICED = "priced"
    FREE = "free"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    """Whether a price or quota observation can support a routing decision."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    INVALID = "invalid"


UnknownHandling = Literal["allow", "refuse"]
"""Operator choice for an unknown or stale advisory observation."""


@dataclass(frozen=True)
class RoutingPolicy:
    """Operator policy for unbounded, advisory routing.

    The defaults admit unknown observations to preserve existing unbounded
    orchestration, but rank them behind current evidence. A task with a cost
    ceiling always requires a current, comparable price regardless of policy.

    Parameters
    ----------
    unknown_price, unknown_quota, stale_price, stale_quota : {"allow", "refuse"}
        Whether an unbounded task may consider the corresponding evidence state.
    """

    unknown_price: UnknownHandling = "allow"
    unknown_quota: UnknownHandling = "allow"
    stale_price: UnknownHandling = "allow"
    stale_quota: UnknownHandling = "allow"

    def __post_init__(self) -> None:
        """Reject invalid operator values instead of silently admitting a route."""
        for choice in (
            self.unknown_price,
            self.unknown_quota,
            self.stale_price,
            self.stale_quota,
        ):
            if choice not in ("allow", "refuse"):
                raise ValueError("routing uncertainty policy must be 'allow' or 'refuse'")


def price_status(
    kind: PriceKind,
    price: ModelPrice | None,
    *,
    observed_at: float | None,
    valid_until: float | None,
    now: float,
) -> EvidenceStatus:
    """Validate a quote and classify its freshness at ``now``.

    A quoted zero is still a price quotation; free use needs an explicit
    ``FREE`` declaration. Invalid values never enter a ranking.
    """
    if kind is PriceKind.UNKNOWN:
        return EvidenceStatus.INVALID if price is not None else EvidenceStatus.UNKNOWN
    if kind is PriceKind.FREE:
        if price is not None:
            return EvidenceStatus.INVALID
    elif price is None or not all(
        math.isfinite(value) and value >= 0 for value in (price.input_per_1k, price.output_per_1k)
    ):
        return EvidenceStatus.INVALID
    return _freshness(observed_at, valid_until, now)


def quota_status(
    utilisation: float | None,
    *,
    observed_at: float | None,
    valid_until: float | None,
    now: float,
) -> EvidenceStatus:
    """Validate utilisation in ``[0, 1]`` and classify its freshness."""
    if utilisation is None:
        return EvidenceStatus.UNKNOWN
    if not math.isfinite(utilisation) or not 0 <= utilisation <= 1:
        return EvidenceStatus.INVALID
    return _freshness(observed_at, valid_until, now)


def _freshness(observed_at: float | None, valid_until: float | None, now: float) -> EvidenceStatus:
    """Return current, stale, or invalid for optional observation timestamps."""
    if not math.isfinite(now) or now < 0:
        return EvidenceStatus.INVALID
    for value in (observed_at, valid_until):
        if value is not None and (not math.isfinite(value) or value < 0):
            return EvidenceStatus.INVALID
    if observed_at is not None and observed_at > now:
        return EvidenceStatus.INVALID
    if valid_until is not None and observed_at is not None and valid_until < observed_at:
        return EvidenceStatus.INVALID
    if valid_until is not None and now >= valid_until:
        return EvidenceStatus.STALE
    return EvidenceStatus.CURRENT
