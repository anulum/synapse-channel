# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authentication and access-control configuration record for SynapseHub
"""Authentication, per-message authentication, and ACL configuration record."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.capability_card_trust import CapabilityCardTrustBundle
from synapse_channel.core.hub_defaults import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_STOP_BOUND_SECONDS,
)
from synapse_channel.core.identity_enrollments import (
    DEFAULT_ENROLLMENT_RATE,
    DEFAULT_ENROLLMENT_WINDOW_SECONDS,
)
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureTrustBundle,
    MessageAuthKey,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.role_grants import RoleGrants

__all__ = ["HubAuthConfig"]


@dataclass(frozen=True, kw_only=True)
class HubAuthConfig:
    """Connection authentication, per-message authentication, and ACL enforcement.

    Everything here defaults to the open loopback posture: no token, no
    signed frames, no ACL. Each mechanism is opt-in and composes with the
    others exactly as the flat keyword surface documents.
    """

    authenticator: TokenAuthenticator | None = None
    auth_timeout: float = DEFAULT_AUTH_TIMEOUT
    insecure_off_loopback: bool = False
    insecure_plaintext_at_rest: bool = False
    per_message_auth_keys: Mapping[str, MessageAuthKey] | list[MessageAuthKey] | None = None
    require_per_message_auth: bool = False
    per_message_auth_window_seconds: float = DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS
    per_message_auth_replay_capacity: int = 4096
    per_message_auth_replay_store: DurableMessageAuthReplayStore | None = None
    per_message_auth_sequence_floor_mode: SequenceFloorMode | str = SequenceFloorMode.OFF
    signed_event_trust_bundle: EventSignatureTrustBundle | None = None
    capability_card_trust_bundle: CapabilityCardTrustBundle | None = None
    acl_policy: AclPolicy | None = None
    require_acl: bool = False
    role_grants: RoleGrants | None = None
    # Live approval-load state (core.approval_load.ApprovalLoad) for a declared policy.
    # Held as ``object`` because this module is at its structure-budget ceiling of 31
    # internal imports; normalisation refuses any non-None value that is not an
    # ApprovalLoad (TypeError), and the hub's view narrows it to ``ApprovalLoad | None``.
    approval_load: object = None
    # Validated name/project/'*' -> custodian entries (see core.mailbox_custodians).
    mailbox_custodians: Mapping[str, str] | None = None
    require_role_claim: bool = False
    require_fencing_epoch: bool = False
    require_signed_frames: bool = False
    require_identity_proof: bool = False
    identity_trust_bundle: EventSignatureTrustBundle | None = None
    require_identity_binding: bool = False
    identity_pin_path: str | Path | None = None
    identity_enrollment_path: str | Path | None = None
    identity_enrollment_namespaces: tuple[str, ...] = ()
    identity_enrollment_rate: int = DEFAULT_ENROLLMENT_RATE
    identity_enrollment_window_seconds: float = DEFAULT_ENROLLMENT_WINDOW_SECONDS
    private_directed_messages: bool = False
    verdict_authority: Mapping[str, frozenset[str]] = field(default_factory=dict)
    verdict_gate_enabled: bool = True
    allow_unrecorded_verdict_inputs: bool = False
    ladder_enabled: bool = False
    stop_bound_seconds: float = DEFAULT_STOP_BOUND_SECONDS
