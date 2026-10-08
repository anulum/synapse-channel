# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed compatibility projections for the routing settings family."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
from synapse_channel.core.durable_ingress import DurableIngressQuota
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.hub_config_attribute import ConfigAttribute, HubConfigOwner
from synapse_channel.core.message_forward_transport import MessageForwarder
from synapse_channel.core.multihub_claim_transport import ClaimForwarder
from synapse_channel.core.multihub_serving import PeerCertificateSource
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import RelayForwarder
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.spend_ledger import SpendLedger


class HubRoutingConfigView(HubConfigOwner):
    """Read and replace record-owned settings through their existing hub names."""

    checkpoint_interval: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.checkpoint_interval,
        lambda config, value: replace(config, checkpoint_interval=value),
    )
    claim_forwarder: ConfigAttribute[ClaimForwarder] = ConfigAttribute(
        lambda config: config.multihub.claim_forwarder,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, claim_forwarder=value)
        ),
    )
    dead_letter_forwarder: ConfigAttribute[DeadLetterForwarder | None] = ConfigAttribute(
        lambda config: config.multihub.dead_letter_forwarder,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, dead_letter_forwarder=value)
        ),
    )
    durable_ingress_quota: ConfigAttribute[DurableIngressQuota | None] = ConfigAttribute(
        lambda config: config.durable_ingress_quota,
        lambda config, value: replace(config, durable_ingress_quota=value),
    )
    federation_bundle: ConfigAttribute[FederationBundle | None] = ConfigAttribute(
        lambda config: config.federation.federation_bundle,
        lambda config, value: replace(
            config, federation=replace(config.federation, federation_bundle=value)
        ),
    )
    federation_cert_source: ConfigAttribute[PeerCertificateSource] = ConfigAttribute(
        lambda config: config.federation.federation_cert_source,
        lambda config, value: replace(
            config, federation=replace(config.federation, federation_cert_source=value)
        ),
    )
    host_rate_limiter: ConfigAttribute[RateLimiter | None] = ConfigAttribute(
        lambda config: config.host_rate_limiter,
        lambda config, value: replace(config, host_rate_limiter=value),
    )
    message_forward_ttl: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.multihub.message_forward_ttl,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, message_forward_ttl=value)
        ),
    )
    message_forwarder: ConfigAttribute[MessageForwarder] = ConfigAttribute(
        lambda config: config.multihub.message_forwarder,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, message_forwarder=value)
        ),
    )
    namespace_ownership: ConfigAttribute[NamespaceOwnership | None] = ConfigAttribute(
        lambda config: config.multihub.namespace_ownership,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, namespace_ownership=value)
        ),
    )
    observed_asserting_hubs: ConfigAttribute[Callable[[str], Iterable[str]] | None] = (
        ConfigAttribute(
            lambda config: config.multihub.observed_asserting_hubs,
            lambda config, value: replace(
                config, multihub=replace(config.multihub, observed_asserting_hubs=value)
            ),
        )
    )
    rate_limiter: ConfigAttribute[RateLimiter | None] = ConfigAttribute(
        lambda config: config.rate_limiter,
        lambda config, value: replace(config, rate_limiter=value),
    )
    relay_forwarder: ConfigAttribute[RelayForwarder] = ConfigAttribute(
        lambda config: config.multihub.relay_forwarder,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, relay_forwarder=value)
        ),
    )
    relay_max_lines: ConfigAttribute[int] = ConfigAttribute(
        lambda config: config.relay_max_lines,
        lambda config, value: replace(config, relay_max_lines=value),
    )
    require_relay_reason: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.multihub.require_relay_reason,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, require_relay_reason=value)
        ),
    )
    require_two_person_relay: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.multihub.require_two_person_relay,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, require_two_person_relay=value)
        ),
    )
    shutdown_close_timeout: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.shutdown_close_timeout,
        lambda config, value: replace(config, shutdown_close_timeout=value),
    )
    spend_ledger: ConfigAttribute[SpendLedger | None] = ConfigAttribute(
        lambda config: config.multihub.spend_ledger,
        lambda config, value: replace(
            config, multihub=replace(config.multihub, spend_ledger=value)
        ),
    )
