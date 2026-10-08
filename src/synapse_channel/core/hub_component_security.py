# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — authentication and identity collaborators
"""Construct replay protection, identity enrollment and pinning independently."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.identity_enrollments import (
    EnrollmentRateLimiter,
    load_enrolled_keys,
    merge_enrolled_keys,
)
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.message_auth import (
    DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS,
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
)
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.numeric_coercion import safe_float, safe_int


@dataclass(frozen=True, kw_only=True)
class HubSecurityComponents:
    """Mutable security services and their normalized live identity inputs."""

    message_keys: dict[str, MessageAuthKey]
    sequence_floor: SequenceFloorMode
    message_replay: MessageReplayCache
    identity_trust: EventSignatureTrustBundle | None
    enrollment_path: Path | None
    enrolled_keys: dict[str, EventSignatureKey]
    enrollment_namespaces: frozenset[str]
    enrollment_rate: EnrollmentRateLimiter
    pin_path: Path | None
    pins: IdentityPinStore


def build_security(config: HubConfig) -> HubSecurityComponents:
    """Build security state, refusing enrollment without trust and durability."""
    auth = config.auth
    keys = (
        dict(auth.per_message_auth_keys)
        if isinstance(auth.per_message_auth_keys, Mapping)
        else {key.key_id: key for key in auth.per_message_auth_keys or []}
    )
    floor = SequenceFloorMode(auth.per_message_auth_sequence_floor_mode)
    replay = MessageReplayCache(
        window_seconds=safe_float(
            auth.per_message_auth_window_seconds, default=DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS
        ),
        max_entries=safe_int(auth.per_message_auth_replay_capacity, default=4096, min_value=1),
        durable=auth.per_message_auth_replay_store,
        sequence_floor_mode=floor,
    )
    if auth.identity_enrollment_path and (
        auth.identity_trust_bundle is None or config.journal is None
    ):
        raise ValueError(
            "online identity enrolment needs an identity trust bundle and a durable "
            "journal: pass --identity-trust and --db with --identity-enrollments"
        )
    enrollment_path = (
        Path(auth.identity_enrollment_path).expanduser() if auth.identity_enrollment_path else None
    )
    enrolled = load_enrolled_keys(enrollment_path) if enrollment_path is not None else {}
    trust = (
        merge_enrolled_keys(auth.identity_trust_bundle, enrolled)
        if auth.identity_trust_bundle is not None and enrollment_path is not None
        else auth.identity_trust_bundle
    )
    pin_path = Path(auth.identity_pin_path).expanduser() if auth.identity_pin_path else None
    return HubSecurityComponents(
        message_keys=keys,
        sequence_floor=floor,
        message_replay=replay,
        identity_trust=trust,
        enrollment_path=enrollment_path,
        enrolled_keys=enrolled,
        enrollment_namespaces=frozenset(
            namespace.strip()
            for namespace in auth.identity_enrollment_namespaces
            if namespace.strip()
        ),
        enrollment_rate=EnrollmentRateLimiter(
            limit=max(0, int(auth.identity_enrollment_rate)),
            window_seconds=max(0.0, float(auth.identity_enrollment_window_seconds)),
        ),
        pin_path=pin_path,
        pins=IdentityPinStore(path=pin_path),
    )
