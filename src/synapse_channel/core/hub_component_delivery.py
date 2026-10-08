# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — delivery and connection gate composition
"""Build delivery services and gates against one explicit client graph."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_channel.core.agent_liveness import RecipientLiveness
from synapse_channel.core.dead_letters import DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS, DeadLetterLedger
from synapse_channel.core.hub_broadcast import HubBroadcaster
from synapse_channel.core.hub_component_callbacks import HubComponentCallbacks
from synapse_channel.core.hub_component_clients import HubClientComponents
from synapse_channel.core.hub_component_lifetime import HubLifetimeComponents
from synapse_channel.core.hub_component_routing import HubRoutingComponents
from synapse_channel.core.hub_component_security import HubSecurityComponents
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.hub_connection import HubConnection
from synapse_channel.core.hub_frame_gates import HubFrameGates
from synapse_channel.core.hub_identity_gate import HubIdentityGate
from synapse_channel.core.hub_ingress import HubIngress
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.operator_relay_forwarding import OperatorRelayForwarding
from synapse_channel.core.pending_receipts import PendingReceipts


@dataclass(frozen=True, kw_only=True)
class HubDeliveryComponents:
    """One hub's delivery bookkeeping and broadcaster."""

    dead_letters: DeadLetterLedger
    pending_receipts: PendingReceipts
    mailbox_pending: MailboxPendingTracker
    relay: RelayMirror
    broadcaster: HubBroadcaster


@dataclass(frozen=True, kw_only=True)
class HubGateComponents:
    """Ingress, identity, connection and forwarding gates sharing the graph."""

    ingress: HubIngress
    identity: HubIdentityGate
    connection: HubConnection
    frames: HubFrameGates
    relay_forwarding: OperatorRelayForwarding


def build_delivery(
    config: HubConfig,
    clients: HubClientComponents,
    lifetime: HubLifetimeComponents,
    callbacks: HubComponentCallbacks,
) -> HubDeliveryComponents:
    """Build delivery services without invoking the deferred transport callbacks."""
    relay = RelayMirror(lifetime.relay_log, config.relay_max_lines)
    return HubDeliveryComponents(
        dead_letters=DeadLetterLedger(max_age_seconds=DEFAULT_DEAD_LETTER_MAX_AGE_SECONDS),
        pending_receipts=PendingReceipts(),
        mailbox_pending=MailboxPendingTracker(config.journal),
        relay=relay,
        broadcaster=HubBroadcaster(
            clients.clients, relay, system=callbacks.system, online_agents=callbacks.online_agents
        ),
    )


def build_connection(
    config: HubConfig,
    clients: HubClientComponents,
    reactions: RecipientLiveness,
    callbacks: HubComponentCallbacks,
) -> HubConnection:
    """Wire runtime connection cleanup to the target and its actual registries."""
    return HubConnection(
        clients.clients,
        clients.capabilities,
        authenticator=config.auth.authenticator,
        auth_timeout=config.auth.auth_timeout,
        rate_limiter=config.rate_limiter,
        handle_message=callbacks.handle_message,
        send_json=callbacks.send_json,
        system=callbacks.system,
        online_agents=callbacks.online_agents,
        broadcast_presence=callbacks.broadcast_presence,
        drop_waits=callbacks.drop_waits,
        forget_liveness=reactions.forget,
        abort_uploads=config.attachment_store.abort_sender if config.attachment_store else None,
        agent_left=callbacks.agent_left,
    )


def build_frame_gates(
    config: HubConfig,
    clients: HubClientComponents,
    security: HubSecurityComponents,
    routing: HubRoutingComponents,
    hub_id: str,
    callbacks: HubComponentCallbacks,
) -> HubFrameGates:
    """Share replay protection, counters and peer inputs with the frame gates."""
    return HubFrameGates(
        require_per_message_auth=config.auth.require_per_message_auth,
        per_message_auth_keys=security.message_keys,
        message_replay=security.message_replay,
        signed_event_trust_bundle=config.auth.signed_event_trust_bundle,
        require_acl=config.auth.require_acl,
        acl_policy=config.auth.acl_policy,
        namespace_ownership=config.multihub.namespace_ownership,
        observed_asserting_hubs=config.multihub.observed_asserting_hubs,
        claim_peers=routing.claim_peers,
        claim_forwarder=config.multihub.claim_forwarder,
        counters=clients.counters,
        hub_id=hub_id,
        send_json=callbacks.send_json,
        system=callbacks.system,
    )


def build_gates(
    config: HubConfig,
    clients: HubClientComponents,
    security: HubSecurityComponents,
    routing: HubRoutingComponents,
    reactions: RecipientLiveness,
    hub_id: str,
    callbacks: HubComponentCallbacks,
) -> HubGateComponents:
    """Construct all admission gates without an implicit hub dependency."""
    return HubGateComponents(
        ingress=HubIngress(
            clients.clients,
            authenticator=config.auth.authenticator,
            enable_metrics=config.metrics.enable_metrics,
            metrics_token=config.metrics.metrics_token,
            metrics_query_token_ok=config.metrics.metrics_query_token_ok,
            insecure_off_loopback=config.auth.insecure_off_loopback,
            send_json=callbacks.send_json,
            system=callbacks.system,
        ),
        identity=HubIdentityGate(
            require_identity_binding=config.auth.require_identity_binding,
            identity_trust_bundle=security.identity_trust,
            send_json=callbacks.send_json,
            system=callbacks.system,
            pin_store=security.pins,
        ),
        connection=build_connection(config, clients, reactions, callbacks),
        frames=build_frame_gates(config, clients, security, routing, hub_id, callbacks),
        relay_forwarding=OperatorRelayForwarding(
            namespace_ownership=config.multihub.namespace_ownership,
            relay_peers=routing.relay_peers,
            relay_forwarder=config.multihub.relay_forwarder,
            observed_asserting_hubs=config.multihub.observed_asserting_hubs,
            hub_id=hub_id,
            journal=config.journal,
            send_json=callbacks.send_json,
            system=callbacks.system,
        ),
    )
