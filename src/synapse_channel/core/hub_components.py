# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — complete typed graph received by a hub
"""Describe a fully composed hub graph, with configuration and target custody."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_component_clients import HubClientComponents
from synapse_channel.core.hub_component_delivery import HubDeliveryComponents, HubGateComponents
from synapse_channel.core.hub_component_lifetime import HubLifetimeComponents
from synapse_channel.core.hub_component_routing import HubRoutingComponents
from synapse_channel.core.hub_component_security import HubSecurityComponents
from synapse_channel.core.hub_component_state import HubStateComponents
from synapse_channel.core.hub_config import HubConfig


@dataclass(frozen=True, kw_only=True)
class HubComponents:
    """All constructed dependencies, scoped to one requested record and target.

    Configuration normalization is separate from the requested posture used to
    fingerprint the start. Component records freeze bindings; their actual
    registries remain mutable. Supplied journals, replay stores and attachments
    retain caller ownership. Only ``lifetime.live_checkpoint`` transfers to the
    target after successful installation.
    """

    requested: HubConfig
    configuration: HubConfig
    callbacks: HubComponentCallbacks
    hub_id: str
    lifetime: HubLifetimeComponents
    security: HubSecurityComponents
    clients: HubClientComponents
    routing: HubRoutingComponents
    delivery: HubDeliveryComponents
    gates: HubGateComponents
    state: HubStateComponents
