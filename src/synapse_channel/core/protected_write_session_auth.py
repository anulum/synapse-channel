# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — enrolled protected session authentication
"""Authenticate the existing protected wire envelope against operator enrollment.

This module does not admit claims, dispatch writes or prove OS credential custody.
Callers retain the current enrollment registry, replay store and trusted clock.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    verify_frame,
)
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import (
    ParsedProtectedWriteRequest,
    parse_protected_write_request,
)

_VERBS = frozenset({"prepare", "admit", "begin", "settle", "revoke", "status", "recover"})


@dataclass(frozen=True)
class ProtectedSessionEnrollment:
    """Operator-owned exact session scope; never accept this object from a client."""

    session_id: str
    principal: str
    target: str
    authority_id: str
    authority_continuity: str
    enrollment_revision: str
    transaction_id: str
    proposal_sha256: str
    verbs: frozenset[str]
    expires_at: float
    key: MessageAuthKey = field(repr=False)
    revoked: bool = False

    def __post_init__(self) -> None:
        """Refuse ambiguous enrollment and shared multi-sender credentials."""
        identifiers = (
            self.session_id,
            self.principal,
            self.target,
            self.authority_id,
            self.authority_continuity,
            self.enrollment_revision,
            self.transaction_id,
        )
        if any(type(value) is not str or not value for value in identifiers):
            raise ValueError("session enrollment requires nonempty identifiers")
        if (
            type(self.proposal_sha256) is not str
            or len(self.proposal_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.proposal_sha256)
        ):
            raise ValueError("session enrollment requires an exact proposal digest")
        if type(self.verbs) is not frozenset or not self.verbs or not self.verbs <= _VERBS:
            raise ValueError("session enrollment requires explicit supported verbs")
        if type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at):
            raise ValueError("session expiry must be finite")
        if (
            type(self.revoked) is not bool
            or type(self.key.secret) is not bytes
            or len(self.key.secret) < 32
            or not self.key.key_id
            or self.key.senders != frozenset({self.principal})
        ):
            raise ValueError("session requires its own strong single-principal key")


@dataclass(frozen=True)
class AuthenticatedProtectedRequest:
    """Ingress result retained internally, never a client-supplied capability."""

    parsed: ParsedProtectedWriteRequest
    enrollment: ProtectedSessionEnrollment = field(repr=False)
    authenticated_at: float
    authentication_expires_at: float


def recheck_authenticated_protected_request(
    authenticated: AuthenticatedProtectedRequest,
    *,
    enrollments: Mapping[str, ProtectedSessionEnrollment],
    authenticated_principal: str,
    now: float,
) -> None:
    """Refuse revoked, replaced or expired ingress enrollment before mutation.

    Parameters
    ----------
    authenticated:
        Actual ingress result retained by the server, never deserialized from IPC.
    enrollments:
        Current registry under the authority mutation ordering.
    authenticated_principal:
        Current authenticated principal.
    now:
        Fresh trusted clock.

    Raises
    ------
    ValueError
        If any enrollment field, key material or current scope changed.
    """
    current = _validate_protected_session_scope(
        authenticated.parsed,
        enrollments=enrollments,
        authenticated_principal=authenticated_principal,
        now=now,
    )
    if current != authenticated.enrollment:
        raise ValueError("authenticated session enrollment replaced")
    if now < authenticated.authenticated_at or now > authenticated.authentication_expires_at:
        raise ValueError("authenticated request freshness lost before mutation")


def _validate_protected_session_scope(
    parsed: ParsedProtectedWriteRequest,
    *,
    enrollments: Mapping[str, ProtectedSessionEnrollment],
    authenticated_principal: str,
    now: float,
) -> ProtectedSessionEnrollment:
    """Recheck current enrollment inside authority ordering before mutation.

    Parameters
    ----------
    parsed:
        Previously parsed request, not an authentication capability.
    enrollments:
        Current trusted registry; replacement and revocation must be serialized
        with authority mutation. Never use a captured stale registry snapshot.
    authenticated_principal:
        Principal resolved by the authenticated ingress.
    now:
        Fresh trusted server time.

    Returns
    -------
    ProtectedSessionEnrollment
        Matching current enrollment, not permission to bypass admission.

    Raises
    ------
    ValueError
        For missing, expired, revoked or out-of-scope enrollment.
    """
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("session clock must be finite")
    frame = json.loads(parsed.canonical_bytes)
    enrollment = enrollments.get(frame["session_id"])
    if enrollment is None or enrollment.revoked or enrollment.key.revoked:
        raise ValueError("session unavailable")
    if now >= enrollment.expires_at:
        raise ValueError("session expired")
    expected = {
        "session_id": enrollment.session_id,
        "sender": enrollment.principal,
        "target": enrollment.target,
        "authority_id": enrollment.authority_id,
        "authority_continuity": enrollment.authority_continuity,
        "enrollment_revision": enrollment.enrollment_revision,
        "transaction_id": enrollment.transaction_id,
        "proposal_sha256": enrollment.proposal_sha256,
    }
    if authenticated_principal != enrollment.principal or any(
        frame[name] != value for name, value in expected.items()
    ):
        raise ValueError("request outside current session scope")
    if frame["type"].removeprefix("protected_write_") not in enrollment.verbs:
        raise ValueError("operation outside current session scope")
    auth = frame.get("auth")
    if not isinstance(auth, dict) or auth.get("kid") != enrollment.key.key_id:
        raise ValueError("request does not bind the enrolled session key")
    return enrollment


def authenticate_protected_request(
    raw: str | bytes,
    *,
    limits: ProtectedWriteProposalLimits,
    enrollments: Mapping[str, ProtectedSessionEnrollment],
    authenticated_principal: str,
    replay_cache: MessageReplayCache,
    now: float,
) -> AuthenticatedProtectedRequest:
    """Verify exact scope and real per-message HMAC before protected admission.

    Parameters
    ----------
    raw:
        Original bounded wire request; duplicate JSON keys are rejected.
    limits:
        Operator-enrolled protocol representation bounds.
    enrollments:
        Current operator-owned session registry.
    authenticated_principal:
        Transport-resolved principal; a caller-supplied sender is insufficient.
    replay_cache:
        Authority replay cache; deployed continuity requires its durable store.
    now:
        Fresh trusted server time.

    Returns
    -------
    AuthenticatedProtectedRequest
        Authenticated parsed request. Recheck current scope under the actor
        before mutation; this return value is not a persistent capability.

    Raises
    ------
    ValueError
        If parsing, enrollment, signature, freshness or replay validation fails.
    """
    parsed = parse_protected_write_request(raw, limits=limits)
    enrollment = _validate_protected_session_scope(
        parsed,
        enrollments=enrollments,
        authenticated_principal=authenticated_principal,
        now=now,
    )
    result = verify_frame(
        json.loads(parsed.canonical_bytes),
        keys={enrollment.key.key_id: enrollment.key},
        replay_cache=replay_cache,
        now=now,
        required_sender=authenticated_principal,
    )
    if result != VerificationResult.OK:
        raise ValueError(f"protected session authentication refused: {result.value}")
    frame = json.loads(parsed.canonical_bytes)
    expires_at = float(frame["auth"]["timestamp"]) + replay_cache.window_seconds
    return AuthenticatedProtectedRequest(parsed, enrollment, now, expires_at)
