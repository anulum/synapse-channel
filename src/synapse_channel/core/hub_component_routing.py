# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — peer routing and federation collaborators
"""Build the forwarding ledger and federation gate from explicit dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_component_security import HubSecurityComponents
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_federation_gate import HubFederationGate
from synapse_channel.core.message_forward_ledger import MessageForwardLedger
from synapse_channel.core.message_forward_transport import MessageForwardPeer
from synapse_channel.core.multihub_claim_transport import ClaimForwardPeer
from synapse_channel.core.multihub_serving import MultiHubServingPolicy, check_identity_grants
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer


@dataclass(frozen=True, kw_only=True)
class HubRoutingComponents:
    """Peer maps copied for this hub and the live routing services."""

    claim_peers: dict[str, ClaimForwardPeer] | None
    relay_peers: dict[str, OperatorRelayPeer] | None
    message_peers: dict[str, MessageForwardPeer] | None
    message_forward_ledger: MessageForwardLedger
    federation_gate: HubFederationGate
    federation_offer_path: Path | None
    serving_policy: MultiHubServingPolicy | None


def build_routing(
    config: HubConfig, security: HubSecurityComponents, callbacks: HubComponentCallbacks
) -> HubRoutingComponents:
    """Validate identity grants before constructing the forwarding graph."""
    routing = config.multihub
    if routing.multihub_serving_policy is not None:
        check_identity_grants(
            routing.multihub_serving_policy,
            identity_trust_bundle=security.identity_trust,
            require_identity_binding=config.auth.require_identity_binding,
        )
    return HubRoutingComponents(
        serving_policy=routing.multihub_serving_policy,
        claim_peers=dict(routing.claim_peers) if routing.claim_peers else None,
        relay_peers=dict(routing.relay_peers) if routing.relay_peers else None,
        message_peers=dict(routing.message_peers) if routing.message_peers else None,
        message_forward_ledger=(
            config.journal.message_forward
            if config.journal is not None
            else MessageForwardLedger.in_memory()
        ),
        federation_offer_path=(
            Path(config.federation.federation_offer_path)
            if config.federation.federation_offer_path is not None
            else None
        ),
        federation_gate=HubFederationGate(
            config.federation.federation_bundle,
            cert_source=config.federation.federation_cert_source,
            require_per_message_auth=config.auth.require_per_message_auth,
            signed_event_trust=config.auth.signed_event_trust_bundle is not None,
            system=callbacks.system,
            send_json=callbacks.send_json,
        ),
    )
