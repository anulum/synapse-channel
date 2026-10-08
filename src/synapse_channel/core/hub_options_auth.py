# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — central WebSocket hub that routes messages and owns state
"""Typed legacy keyword names; canonical defaults remain in HubConfig."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability_card_trust import CapabilityCardTrustBundle
from synapse_channel.core.message_auth import EventSignatureTrustBundle, MessageAuthKey
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.role_grants import RoleGrants


class HubAuthOptions(TypedDict, total=False):
    """Preserve the original auth keyword value contracts."""

    authenticator: TokenAuthenticator | None
    auth_timeout: float
    insecure_off_loopback: bool
    insecure_plaintext_at_rest: bool
    per_message_auth_keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None
    require_per_message_auth: bool
    per_message_auth_window_seconds: float
    per_message_auth_replay_capacity: int
    per_message_auth_replay_store: DurableMessageAuthReplayStore | None
    per_message_auth_sequence_floor_mode: SequenceFloorMode | str
    signed_event_trust_bundle: EventSignatureTrustBundle | None
    capability_card_trust_bundle: CapabilityCardTrustBundle | None
    acl_policy: AclPolicy | None
    require_acl: bool
    role_grants: RoleGrants | None
    require_role_claim: bool
    require_fencing_epoch: bool
    identity_trust_bundle: EventSignatureTrustBundle | None
    require_identity_binding: bool
    identity_pin_path: str | Path | None
    identity_enrollment_path: str | Path | None
    identity_enrollment_namespaces: tuple[str, ...]
    identity_enrollment_rate: int
    identity_enrollment_window_seconds: float
    private_directed_messages: bool
