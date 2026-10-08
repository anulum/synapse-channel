# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed compatibility projections for the auth settings family."""

from __future__ import annotations

from dataclasses import replace

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability_card_trust import CapabilityCardTrustBundle
from synapse_channel.core.hub_config_attribute import ConfigAttribute, HubConfigOwner
from synapse_channel.core.message_auth import EventSignatureTrustBundle
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.role_grants import RoleGrants


class HubAuthConfigView(HubConfigOwner):
    """Read and replace record-owned settings through their existing hub names."""

    acl_policy: ConfigAttribute[AclPolicy | None] = ConfigAttribute(
        lambda config: config.auth.acl_policy,
        lambda config, value: replace(config, auth=replace(config.auth, acl_policy=value)),
    )
    auth_timeout: ConfigAttribute[float] = ConfigAttribute(
        lambda config: config.auth.auth_timeout,
        lambda config, value: replace(config, auth=replace(config.auth, auth_timeout=value)),
    )
    authenticator: ConfigAttribute[TokenAuthenticator | None] = ConfigAttribute(
        lambda config: config.auth.authenticator,
        lambda config, value: replace(config, auth=replace(config.auth, authenticator=value)),
    )
    capability_card_trust_bundle: ConfigAttribute[CapabilityCardTrustBundle | None] = (
        ConfigAttribute(
            lambda config: config.auth.capability_card_trust_bundle,
            lambda config, value: replace(
                config, auth=replace(config.auth, capability_card_trust_bundle=value)
            ),
        )
    )
    insecure_off_loopback: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.insecure_off_loopback,
        lambda config, value: replace(
            config, auth=replace(config.auth, insecure_off_loopback=value)
        ),
    )
    insecure_plaintext_at_rest: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.insecure_plaintext_at_rest,
        lambda config, value: replace(
            config, auth=replace(config.auth, insecure_plaintext_at_rest=value)
        ),
    )
    per_message_auth_replay_store: ConfigAttribute[DurableMessageAuthReplayStore | None] = (
        ConfigAttribute(
            lambda config: config.auth.per_message_auth_replay_store,
            lambda config, value: replace(
                config, auth=replace(config.auth, per_message_auth_replay_store=value)
            ),
        )
    )
    private_directed_messages: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.private_directed_messages,
        lambda config, value: replace(
            config, auth=replace(config.auth, private_directed_messages=value)
        ),
    )
    require_acl: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.require_acl,
        lambda config, value: replace(config, auth=replace(config.auth, require_acl=value)),
    )
    require_fencing_epoch: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.require_fencing_epoch,
        lambda config, value: replace(
            config, auth=replace(config.auth, require_fencing_epoch=value)
        ),
    )
    require_identity_binding: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.require_identity_binding,
        lambda config, value: replace(
            config, auth=replace(config.auth, require_identity_binding=value)
        ),
    )
    require_per_message_auth: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.require_per_message_auth,
        lambda config, value: replace(
            config, auth=replace(config.auth, require_per_message_auth=value)
        ),
    )
    require_role_claim: ConfigAttribute[bool] = ConfigAttribute(
        lambda config: config.auth.require_role_claim,
        lambda config, value: replace(config, auth=replace(config.auth, require_role_claim=value)),
    )
    role_grants: ConfigAttribute[RoleGrants | None] = ConfigAttribute(
        lambda config: config.auth.role_grants,
        lambda config, value: replace(config, auth=replace(config.auth, role_grants=value)),
    )
    signed_event_trust_bundle: ConfigAttribute[EventSignatureTrustBundle | None] = ConfigAttribute(
        lambda config: config.auth.signed_event_trust_bundle,
        lambda config, value: replace(
            config, auth=replace(config.auth, signed_event_trust_bundle=value)
        ),
    )
