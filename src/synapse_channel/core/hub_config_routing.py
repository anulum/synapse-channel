# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub routing and cross-domain federation configuration records
"""Multi-hub routing and cross-domain federation configuration records for SynapseHub."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.message_forward_origin import DEFAULT_FORWARD_TTL_SECONDS
from synapse_channel.core.message_forward_transport import (
    MessageForwarder,
    MessageForwardPeer,
    forward_message,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardPeer,
    forward_claim,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingPolicy,
    PeerCertificateSource,
    live_peer_certificate_der,
)
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import (
    OperatorRelayPeer,
    RelayForwarder,
    relay_operator_action,
)
from synapse_channel.core.spend_ledger import SpendLedger

__all__ = ["FederationConfig", "MultiHubConfig"]


@dataclass(frozen=True, kw_only=True)
class MultiHubConfig:
    """Multi-hub routing: serving policy, ownership, claim, relay and message forwarding."""

    multihub_serving_policy: MultiHubServingPolicy | None = None
    spend_ledger: SpendLedger | None = None
    namespace_ownership: NamespaceOwnership | None = None
    claim_peers: Mapping[str, ClaimForwardPeer] | None = None
    claim_forwarder: ClaimForwarder = forward_claim
    relay_peers: Mapping[str, OperatorRelayPeer] | None = None
    relay_forwarder: RelayForwarder = relay_operator_action
    message_peers: Mapping[str, MessageForwardPeer] | None = None
    message_forwarder: MessageForwarder = forward_message
    message_forward_ttl: float = DEFAULT_FORWARD_TTL_SECONDS
    require_relay_reason: bool = False
    require_two_person_relay: bool = False
    observed_asserting_hubs: Callable[[str], Iterable[str]] | None = None
    dead_letter_forwarder: DeadLetterForwarder | None = forward_dead_letter


@dataclass(frozen=True, kw_only=True)
class FederationConfig:
    """Cross-domain federation: the peering bundle, certificate reader, and served offer."""

    federation_bundle: FederationBundle | None = None
    federation_cert_source: PeerCertificateSource = live_peer_certificate_der
    federation_offer_path: str | Path | None = None
