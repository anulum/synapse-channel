# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected authority test enrollments
"""Provide explicit key and enrollment examples for real authority journeys."""

from __future__ import annotations

from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment

AUTHOR_KEY = MessageAuthKey("dispatch-author", b"a" * 32, frozenset({"EXAMPLE/author"}))
WRITER_KEY = MessageAuthKey("dispatch-writer", b"w" * 32, frozenset({"EXAMPLE/writer"}))
RECOVERY_KEY = MessageAuthKey("dispatch-recovery", b"r" * 32, frozenset({"EXAMPLE/recovery"}))
AUTHORITY_KEY = MessageAuthKey("dispatch-authority", b"s" * 32, frozenset({"EXAMPLE/authority"}))


def _grant(
    *,
    principal: str,
    session_id: str,
    key: MessageAuthKey,
    verbs: frozenset[str],
    digest: str,
    now: float,
    incarnation: str | None = None,
) -> ProtectedSessionEnrollment:
    """Construct one bounded enrolled example around the supplied actual test key."""
    return ProtectedSessionEnrollment(
        session_id=session_id,
        principal=principal,
        target="EXAMPLE/authority",
        authority_id="authority",
        authority_continuity="continuity",
        enrollment_revision="enrollment",
        transaction_id="transaction",
        proposal_sha256=digest,
        verbs=verbs,
        expires_at=now + 60,
        key=key,
        writer_incarnation=incarnation,
    )
