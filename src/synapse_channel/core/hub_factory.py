# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — explicit external composition root for hub construction
"""Construct a graph before installing it into the routing hub.

The graph has deferred callbacks to its owning hub. Allocate that target first,
build the actual services outside it, then initialize it with those services.
No callback is executed while the target is incomplete. Legacy constructors use
the same composition function, so there is only one implementation of startup.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from typing import TYPE_CHECKING

from synapse_channel.core.agent_liveness import RecipientLiveness
from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_component_clients import build_clients
from synapse_channel.core.hub_component_delivery import build_delivery, build_gates
from synapse_channel.core.hub_component_lifetime import (
    build_lifetime,
    checkpoint_interval,
    initial_checkpoint,
    validate_attachments,
)
from synapse_channel.core.hub_component_normalization import (
    normalize_limits,
    normalize_liveness_and_routing,
    normalize_transport,
)
from synapse_channel.core.hub_component_routing import build_routing
from synapse_channel.core.hub_component_security import build_security
from synapse_channel.core.hub_component_state import build_state
from synapse_channel.core.hub_components import HubComponents
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_constructor_options import resolve_hub_config

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

ComponentFactory = Callable[[HubConfig, HubComponentCallbacks], HubComponents]


def build_components(config: HubConfig, callbacks: HubComponentCallbacks) -> HubComponents:
    """Compose the whole graph, releasing owned resources on any failure."""
    validate_attachments(config)
    normalized = replace(config, checkpoint_interval=checkpoint_interval(config))
    path, live = initial_checkpoint(normalized)
    with ExitStack() as resources:
        if live is not None:
            resources.callback(live.close)
        normalized = normalize_transport(normalized)
        security = build_security(normalized)
        normalized = normalize_liveness_and_routing(normalized)
        reactions = RecipientLiveness(window_seconds=normalized.liveness.recipient_liveness_window)
        routing = build_routing(normalized, security, callbacks)
        lifetime = build_lifetime(normalized, path, live)
        clients = build_clients(normalized, lifetime.clock, lifetime.started)
        normalized = normalize_limits(normalized, clients.clients)
        policy = normalized.multihub.multihub_serving_policy
        if policy is not None:
            routing = replace(
                routing,
                serving_policy=replace(policy, identity_source=clients.clients.identity_proof),
            )
        delivery = build_delivery(normalized, clients, lifetime, callbacks)
        hub_id = config.hub_id or f"syn-{uuid.uuid4().hex[:8]}"
        gates = build_gates(normalized, clients, security, routing, reactions, hub_id, callbacks)
        state = build_state(normalized, clients, delivery, lifetime, reactions, callbacks)
        components = HubComponents(
            requested=config,
            configuration=normalized,
            callbacks=callbacks,
            hub_id=hub_id,
            lifetime=lifetime,
            security=security,
            clients=clients,
            routing=routing,
            delivery=delivery,
            gates=gates,
            state=state,
        )
        resources.pop_all()
    return components


def build_hub(
    config: HubConfig | None = None, *, component_factory: ComponentFactory = build_components
) -> SynapseHub:
    """Build and install one complete graph, without constructor recomposition.

    A custom typed factory can substitute real collaborators while preserving
    the target callbacks and shared graph. The constructor validates custody
    before installing the graph. A refused installation releases the acquired
    checkpoint and leaves all caller-owned resources open.
    """
    from synapse_channel.core.hub import SynapseHub

    requested = resolve_hub_config(config, {})
    hub = SynapseHub.__new__(SynapseHub)
    components = component_factory(requested, hub.component_callbacks())
    try:
        if components.requested is not requested:
            raise ValueError("hub components belong to another configuration record")
        SynapseHub.__init__(hub, components)
    except BaseException:
        if components.callbacks.owner is hub and components.lifetime.live_checkpoint is not None:
            components.lifetime.live_checkpoint.close()
        raise
    return hub
