# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — resource-offer registry for coordination state
"""Resource-offer accounting for the coordination state."""

from __future__ import annotations

from typing import Any

from synapse_channel.core.state_models import ResourceOffer

DEFAULT_RESOURCE_TTL_SECONDS = 300.0
"""Soft liveness window after which an un-refreshed resource offer is dropped."""

MAX_OFFERS_PER_AGENT = 64
"""Most live resource offers one agent may register."""


class ResourceRegistry:
    """Store, refresh, query, and expire resource offers."""

    def __init__(
        self,
        *,
        max_offers_per_agent: int = MAX_OFFERS_PER_AGENT,
        resources: dict[str, ResourceOffer] | None = None,
    ) -> None:
        self.max_offers_per_agent = max(1, int(max_offers_per_agent))
        self.resources: dict[str, ResourceOffer] = {} if resources is None else resources

    def offers_by(self, agent: str) -> int:
        """Return how many live resource offers ``agent`` currently holds."""
        return sum(1 for offer in self.resources.values() if offer.agent == agent)

    def offer(
        self,
        agent: str,
        *,
        kind: str,
        name: str,
        capacity: int = 1,
        meta: dict[str, Any] | None = None,
        now: float,
    ) -> str | None:
        """Store or refresh an offer, returning its key when accepted."""
        key = f"{agent}:{kind}:{name}"
        if key not in self.resources and self.offers_by(agent) >= self.max_offers_per_agent:
            return None
        self.resources[key] = ResourceOffer(
            agent=agent,
            kind=kind,
            name=name,
            capacity=max(1, int(capacity)),
            meta=meta or {},
            offered_at=now,
        )
        return key

    def query(self, kind: str | None = None) -> list[dict[str, Any]]:
        """List currently offered resources, optionally filtered by kind."""
        out: list[dict[str, Any]] = []
        for offer in self.resources.values():
            if kind is None or offer.kind == kind:
                out.append(
                    {
                        "agent": offer.agent,
                        "kind": offer.kind,
                        "name": offer.name,
                        "capacity": offer.capacity,
                        "meta": offer.meta,
                    }
                )
        return sorted(out, key=lambda r: (r["agent"], r["kind"], r["name"]))

    def expire(self, now: float, ttl: float = DEFAULT_RESOURCE_TTL_SECONDS) -> None:
        """Drop resource offers not refreshed within ``ttl`` seconds of ``now``."""
        stale = [key for key, offer in self.resources.items() if (now - offer.offered_at) > ttl]
        for key in stale:
            del self.resources[key]
