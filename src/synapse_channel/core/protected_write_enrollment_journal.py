# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable protected session enrollment metadata
"""Replay secret-free issue and revoke records before protected operations."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.protected_write_session_scope import ProtectedSessionScope

if TYPE_CHECKING:
    from synapse_channel.core.state import SynapseState

ISSUE_EVENT_KIND = "protected_write_enrollment_issue"
REVOKE_EVENT_KIND = "protected_write_enrollment_revoke"
_SCHEMA = "synapse-protected-write.enrollment.v1"
_FINGERPRINT_DOMAIN = b"synapse-enrollment-key-fp:v1\x00"
_ISSUE_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "principal",
        "target",
        "authority_id",
        "authority_continuity",
        "enrollment_revision",
        "transaction_id",
        "proposal_sha256",
        "verbs",
        "expires_at",
        "key_id",
        "key_fingerprint",
        "writer_incarnation",
    }
)


def key_fingerprint(secret: bytes) -> str:
    """Fingerprint an enrolled secret without recording its bytes.

    Parameters
    ----------
    secret : bytes
        Operator-held key material with at least 256 bits.

    Returns
    -------
    str
        Domain-separated SHA-256 fingerprint for retained key-reuse checks.

    Raises
    ------
    ValueError
        If key material is not bytes or is shorter than 32 bytes.
    """
    if type(secret) is not bytes or len(secret) < 32:
        raise ValueError("enrollment key must contain at least 256 bits")
    return hashlib.sha256(_FINGERPRINT_DOMAIN + secret).hexdigest()


@dataclass(frozen=True)
class ProtectedEnrollmentRecord(ProtectedSessionScope):
    """Retain validated session scope and credential identity without secret bytes.

    Parameters
    ----------
    key_id : str
        Exact operator-provisioned key name retained for this session.
    key_fingerprint : str
        Domain-separated lowercase SHA-256 fingerprint of that key's bytes.
    revoked : bool, optional
        Whether a retained revocation has withdrawn the issued session.

    See Also
    --------
    ProtectedSessionScope
        Inherited authority, transaction, proposal, verb and lifetime fields.
    """

    key_id: str
    key_fingerprint: str
    revoked: bool = False

    def __post_init__(self) -> None:
        """Validate retained metadata without constructing a session credential."""
        super().__post_init__()
        if (
            type(self.key_fingerprint) is not str
            or len(self.key_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.key_fingerprint)
        ):
            raise ValueError("invalid protected enrollment fingerprint")
        if type(self.key_id) is not str or not self.key_id:
            raise ValueError("invalid protected enrollment key id")
        if type(self.revoked) is not bool:
            raise ValueError("protected enrollment revocation flag must be boolean")

    def materialize(self, key: MessageAuthKey, *, now: float) -> ProtectedSessionEnrollment:
        """Bind retained metadata to the current operator-held session key.

        Parameters
        ----------
        key : MessageAuthKey
            Actual retained key, scoped to this record's principal.
        now : float
            Trusted wall-clock seconds used to refuse expired records.

        Returns
        -------
        ProtectedSessionEnrollment
            Validated enrollment with the supplied matching key.

        Raises
        ------
        ValueError
            If the record is revoked or expired, the clock is invalid or the
            supplied key differs in identity, principal, revocation or bytes.
        """
        if self.revoked or type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("enrollment unavailable")
        if now >= self.expires_at or key.key_id != self.key_id:
            raise ValueError("enrollment expired or key id mismatched")
        if key.revoked or key.senders != frozenset({self.principal}):
            raise ValueError("enrollment key scope mismatched")
        if key_fingerprint(key.secret) != self.key_fingerprint:
            raise ValueError("enrollment key fingerprint mismatched")
        return ProtectedSessionEnrollment(
            session_id=self.session_id,
            principal=self.principal,
            target=self.target,
            authority_id=self.authority_id,
            authority_continuity=self.authority_continuity,
            enrollment_revision=self.enrollment_revision,
            transaction_id=self.transaction_id,
            proposal_sha256=self.proposal_sha256,
            verbs=self.verbs,
            expires_at=self.expires_at,
            key=key,
            writer_incarnation=self.writer_incarnation,
        )


def enrollment_issue_payload(enrollment: ProtectedSessionEnrollment) -> dict[str, object]:
    """Create closed journal metadata from an operator-provisioned enrollment.

    Parameters
    ----------
    enrollment : ProtectedSessionEnrollment
        Validated operator-supplied session, including its bound incarnation.

    Returns
    -------
    dict of str to object
        Secret-free metadata with the key fingerprint and sorted verb set.

    Raises
    ------
    ValueError
        If the enrollment's key lacks the required fingerprint input strength.
    """
    return {
        "schema_version": _SCHEMA,
        "session_id": enrollment.session_id,
        "principal": enrollment.principal,
        "target": enrollment.target,
        "authority_id": enrollment.authority_id,
        "authority_continuity": enrollment.authority_continuity,
        "enrollment_revision": enrollment.enrollment_revision,
        "transaction_id": enrollment.transaction_id,
        "proposal_sha256": enrollment.proposal_sha256,
        "verbs": sorted(enrollment.verbs),
        "expires_at": enrollment.expires_at,
        "key_id": enrollment.key.key_id,
        "key_fingerprint": key_fingerprint(enrollment.key.secret),
        "writer_incarnation": enrollment.writer_incarnation,
    }


def _decode_issue_record(payload: Mapping[str, Any]) -> ProtectedEnrollmentRecord:
    """Validate retained issue metadata without admitting a new session key."""
    if payload.keys() != _ISSUE_FIELDS or payload["schema_version"] != _SCHEMA:
        raise ValueError("unsupported protected enrollment issue schema")
    verbs = payload["verbs"]
    if type(verbs) is not list or not verbs or any(type(v) is not str for v in verbs):
        raise ValueError("invalid protected enrollment verbs")
    if verbs != sorted(set(verbs)):
        raise ValueError("protected enrollment verbs must be sorted and unique")
    return ProtectedEnrollmentRecord(
        session_id=payload["session_id"],
        principal=payload["principal"],
        target=payload["target"],
        authority_id=payload["authority_id"],
        authority_continuity=payload["authority_continuity"],
        enrollment_revision=payload["enrollment_revision"],
        transaction_id=payload["transaction_id"],
        proposal_sha256=payload["proposal_sha256"],
        verbs=frozenset(verbs),
        expires_at=payload["expires_at"],
        key_id=payload["key_id"],
        key_fingerprint=payload["key_fingerprint"],
        writer_incarnation=payload["writer_incarnation"],
    )


def _apply_issue_record(record: ProtectedEnrollmentRecord, state: SynapseState) -> None:
    """Restore one unique issue after checking the complete enrollment prefix."""
    if record.session_id in state.protected_enrollments:
        raise ValueError("protected session id reused")
    if record.key_id in state.protected_enrollment_key_ids:
        raise ValueError("protected key id reused")
    if record.key_fingerprint in state.protected_enrollment_fingerprints:
        raise ValueError("protected key fingerprint reused")
    if record.writer_incarnation is not None and any(
        not old.revoked and old.principal == record.principal and old.writer_incarnation is not None
        for old in state.protected_enrollments.values()
    ):
        raise ValueError("writer principal already has an active incarnation")
    state.protected_enrollments[record.session_id] = record
    state.protected_enrollment_key_ids.add(record.key_id)
    state.protected_enrollment_fingerprints.add(record.key_fingerprint)


def apply_enrollment_event(event: StoredEvent, state: SynapseState) -> None:
    """Reconstruct an exact issue/revoke prefix or refuse corrupted history.

    Parameters
    ----------
    event : StoredEvent
        Next retained enrollment fact in authoritative journal order.
    state : SynapseState
        Replay projection or isolated mutation candidate receiving this fact.

    Raises
    ------
    ValueError
        If the fact is unsupported, malformed, reused or has no predecessor.
        A refused issue leaves the enrollment projection unchanged.
    """
    payload: Mapping[str, Any] = event.payload
    if event.kind == ISSUE_EVENT_KIND:
        _apply_issue_record(_decode_issue_record(payload), state)
        return
    if event.kind == REVOKE_EVENT_KIND:
        if (
            payload.keys() != {"schema_version", "session_id"}
            or payload["schema_version"] != _SCHEMA
        ):
            raise ValueError("unsupported protected enrollment revoke schema")
        session_id = payload["session_id"]
        if type(session_id) is not str or session_id not in state.protected_enrollments:
            raise ValueError("protected enrollment revoke has no predecessor")
        previous = state.protected_enrollments[session_id]
        if previous.revoked:
            raise ValueError("protected enrollment already revoked")
        state.protected_enrollments[session_id] = replace(previous, revoked=True)
        return
    raise ValueError("unsupported protected enrollment event")
