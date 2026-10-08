# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — live client registries and shared connection accounting
"""Assemble client services that share one clock and one counter instance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from synapse_channel.core.capability import CapabilityRegistry
from synapse_channel.core.channels import ChannelRegistry
from synapse_channel.core.claim_holder_presence import ClaimHolderPresence
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_counters import HubCounters


@dataclass(frozen=True, kw_only=True)
class HubClientComponents:
    """The shared registry graph; aliases remain the registry's actual objects."""

    channels: ChannelRegistry
    counters: HubCounters
    clients: HubClientRegistry
    claim_holders: ClaimHolderPresence
    capabilities: CapabilityRegistry
    waits: dict[str, set[str]]


def build_clients(
    config: HubConfig, clock: Callable[[], float], started: float
) -> HubClientComponents:
    """Construct registries once and resume the durable outbox counter."""
    counters = HubCounters()
    if config.journal is not None:
        counters.operation_outbox_pending = config.journal.pending_operation_outbox_count()
    clients = HubClientRegistry(
        counters=counters,
        max_clients=config.limits.max_clients,
        max_unauth_clients=config.limits.max_unauth_clients,
        max_connections_per_host=config.limits.max_connections_per_host,
        takeover_cooldown=config.takeover.takeover_cooldown,
        clock=clock,
        takeover_oscillation_window=config.takeover.takeover_oscillation_window,
        takeover_oscillation_threshold=config.takeover.takeover_oscillation_threshold,
        takeover_quarantine=config.takeover.takeover_quarantine,
        lease_offline_ttl=config.takeover.lease_offline_ttl,
    )
    return HubClientComponents(
        channels=ChannelRegistry(),
        counters=counters,
        clients=clients,
        claim_holders=ClaimHolderPresence(
            clock=clock, started_at=started, window=clients.ownership.offline_ttl
        ),
        capabilities=CapabilityRegistry(trust_bundle=config.auth.capability_card_trust_bundle),
        waits={},
    )
