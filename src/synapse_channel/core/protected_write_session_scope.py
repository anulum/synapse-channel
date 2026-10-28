# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — credential-free protected session scope
"""Validate operator-owned session boundaries independently of credential custody."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

_VERBS = frozenset({"prepare", "admit", "begin", "settle", "revoke", "status", "recover"})


@dataclass(frozen=True)
class ProtectedSessionScope:
    """Describe one exact session scope without holding a credential.

    Parameters
    ----------
    session_id, principal, target : str
        Exact session, requesting principal and destination authority names.
    authority_id, authority_continuity : str
        Operator-enrolled authority identity and continuity generation.
    enrollment_revision : str
        Exact operator enrollment revision admitted for this session.
    transaction_id : str
        Transaction to which requests are confined.
    proposal_sha256 : str
        Lowercase SHA-256 digest of the proposal admitted for that transaction.
    verbs : frozenset of str
        Explicit nonempty subset of the seven protected operation names.
    expires_at : float
        Finite expiry in wall-clock seconds.
    writer_incarnation : str or None, optional
        Exact writer process incarnation, supplied by keyword. A writer
        transition must additionally bind this value during authentication.

    Notes
    -----
    This value validates metadata only. It authenticates no caller and grants
    no permission to execute. Retained records and live enrollments share this
    validation without inventing a key for metadata reconstruction.
    """

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
    writer_incarnation: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        """Reject ambiguous identifiers, digests, verbs, expiry and incarnation."""
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
        if self.writer_incarnation is not None and (
            type(self.writer_incarnation) is not str or not self.writer_incarnation
        ):
            raise ValueError("writer incarnation must be an exact nonempty identifier")
