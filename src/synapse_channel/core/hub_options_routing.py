# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed legacy keyword names; canonical defaults remain in HubConfig."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TypedDict

from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.message_forward_transport import MessageForwarder, MessageForwardPeer
from synapse_channel.core.multihub_claim_transport import ClaimForwarder, ClaimForwardPeer
from synapse_channel.core.multihub_serving import MultiHubServingPolicy, PeerCertificateSource
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer, RelayForwarder
from synapse_channel.core.spend_ledger import SpendLedger


class HubRoutingOptions(TypedDict, total=False):
    """Preserve the original routing keyword value contracts."""

    dead_letter_forwarder: DeadLetterForwarder | None
    takeover_cooldown: float
    takeover_oscillation_window: float
    takeover_oscillation_threshold: int
    takeover_quarantine: float
    lease_offline_ttl: float
    enable_metrics: bool
    metrics_token: str | None
    metrics_query_token_ok: bool
    allowed_origins: tuple[str, ...] | list[str]
    advertised_host: str | None
    warn_stale_recipients: bool
    recipient_liveness_window: float
    waiter_liveness_window: float
    multihub_serving_policy: MultiHubServingPolicy | None
    spend_ledger: SpendLedger | None
    namespace_ownership: NamespaceOwnership | None
    claim_peers: Mapping[str, ClaimForwardPeer] | None
    claim_forwarder: ClaimForwarder
    relay_peers: Mapping[str, OperatorRelayPeer] | None
    relay_forwarder: RelayForwarder
    message_peers: Mapping[str, MessageForwardPeer] | None
    message_forwarder: MessageForwarder
    message_forward_ttl: float
    require_relay_reason: bool
    require_two_person_relay: bool
    observed_asserting_hubs: Callable[[str], Iterable[str]] | None
    federation_bundle: FederationBundle | None
    federation_cert_source: PeerCertificateSource
    federation_offer_path: str | Path | None
