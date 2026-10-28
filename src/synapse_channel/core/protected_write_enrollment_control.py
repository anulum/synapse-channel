# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actor-ordered protected enrollment mutations
"""Internal durable enrollment mutations for the isolated operator endpoint."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import cast

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import MessageAuthKey
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    REVOKE_EVENT_KIND,
    apply_enrollment_event,
    enrollment_issue_payload,
)
from synapse_channel.core.protected_write_session_auth import ProtectedSessionEnrollment
from synapse_channel.core.state import SynapseState

_SCHEMA = "synapse-protected-write.enrollment.v1"
_ISSUE_FIELDS = frozenset(
    {
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
        "writer_incarnation",
    }
)


class ProtectedEnrollmentControl:
    """Project sealed keys and order issue/revoke with the reservation actor.

    This class is only an internal authority primitive. A separate operator
    control listener must authenticate a distinct UID before calling either
    mutation; constructing this class does not install such a listener.

    Parameters
    ----------
    hub : SynapseHub
        Existing authority hub with a complete journal and its mutation actor.
    sealed_keys : Mapping of str to MessageAuthKey
        Operator-held keys indexed by exact session ID. No secret is accepted
        through metadata or written into enrollment events.
    clock : Callable
        Trusted current wall-clock seconds, re-read under the mutation actor.
    """

    def __init__(
        self,
        *,
        hub: SynapseHub,
        sealed_keys: Mapping[str, MessageAuthKey],
        clock: Callable[[], float],
    ) -> None:
        """Bind the existing durable hub, sealed key set and trusted clock."""
        journal = hub.journal
        if journal is None or hub.journal_corrupt_rows:
            raise ValueError("protected enrollment requires a complete durable journal")
        self._hub = hub
        self._journal = journal
        self._keys = dict(sealed_keys)
        self._clock = clock

    def current(self) -> dict[str, ProtectedSessionEnrollment]:
        """Project current records using the matching operator-held keys.

        Returns
        -------
        dict of str to ProtectedSessionEnrollment
            Only unexpired, unrevoked records whose sealed key still matches.

        Raises
        ------
        ValueError
            If the trusted clock cannot provide finite wall-clock seconds.
        """
        now = self._now()
        active: dict[str, ProtectedSessionEnrollment] = {}
        for session_id, record in self._hub.state.protected_enrollments.items():
            key = self._keys.get(session_id)
            if key is None:
                continue
            try:
                active[session_id] = record.materialize(key, now=now)
            except ValueError:
                continue
        return active

    async def issue(self, enrollment: ProtectedSessionEnrollment) -> None:
        """Issue a session through an already authenticated operator path.

        Parameters
        ----------
        enrollment : ProtectedSessionEnrollment
            Exact scope bound to one of the retained operator-held keys.

        Raises
        ------
        ValueError
            If the key differs, the session is expired or revoked, retained
            identity/key reuse is detected, or a writer incarnation is active.
        Exception
            A journal failure, with no publication of the provisional session.

        Notes
        -----
        Expiry is rechecked under the existing hub actor. This API does not
        authenticate an operator or establish OS credential isolation.
        """
        key = self._keys.get(enrollment.session_id)
        if key != enrollment.key or enrollment.revoked:
            raise ValueError("enrollment does not match sealed operator key")
        if enrollment.expires_at <= self._now():
            raise ValueError("cannot issue an expired enrollment")
        await self._commit(ISSUE_EVENT_KIND, enrollment_issue_payload(enrollment))

    async def issue_from_metadata(self, metadata: Mapping[str, object]) -> None:
        """Bind closed operator metadata to the already retained session key.

        Parameters
        ----------
        metadata : Mapping of str to object
            Exact scope fields, key ID and sorted unique verbs; no key bytes.

        Raises
        ------
        ValueError
            If fields, scope, key identity or the durable issue contract fail.
        """
        if metadata.keys() != _ISSUE_FIELDS:
            raise ValueError("operator enrollment metadata fields mismatch")
        session_id = metadata["session_id"]
        if type(session_id) is not str or not session_id:
            raise ValueError("operator enrollment session id invalid")
        key = self._keys.get(session_id)
        if key is None or metadata["key_id"] != key.key_id:
            raise ValueError("operator enrollment sealed key unavailable")
        verbs = metadata["verbs"]
        if type(verbs) is not list or not verbs or any(type(v) is not str for v in verbs):
            raise ValueError("operator enrollment verbs invalid")
        if verbs != sorted(set(verbs)):
            raise ValueError("operator enrollment verbs must be sorted and unique")
        enrollment = ProtectedSessionEnrollment(
            session_id=session_id,
            principal=cast(str, metadata["principal"]),
            target=cast(str, metadata["target"]),
            authority_id=cast(str, metadata["authority_id"]),
            authority_continuity=cast(str, metadata["authority_continuity"]),
            enrollment_revision=cast(str, metadata["enrollment_revision"]),
            transaction_id=cast(str, metadata["transaction_id"]),
            proposal_sha256=cast(str, metadata["proposal_sha256"]),
            verbs=frozenset(verbs),
            expires_at=cast(float, metadata["expires_at"]),
            key=key,
            writer_incarnation=cast(str | None, metadata["writer_incarnation"]),
        )
        await self.issue(enrollment)

    async def revoke(self, session_id: str) -> None:
        """Retain revocation through an authenticated operator control path.

        Parameters
        ----------
        session_id : str
            Exact issued session whose authority is being withdrawn.

        Raises
        ------
        ValueError
            If the identity is invalid, unknown or already revoked.
        Exception
            A journal failure, leaving the prior projection authoritative.
        """
        if type(session_id) is not str or not session_id:
            raise ValueError("revocation requires an exact session id")
        await self._commit(REVOKE_EVENT_KIND, {"schema_version": _SCHEMA, "session_id": session_id})

    def _now(self) -> float:
        """Read and validate one trusted clock observation at the calling boundary."""
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("protected enrollment clock unavailable")
        return now

    async def _commit(self, kind: str, payload: dict[str, object]) -> None:
        def mutate(candidate: SynapseState) -> None:
            now = self._now()
            if kind == ISSUE_EVENT_KIND:
                expiry = payload["expires_at"]
                if type(expiry) not in (int, float) or cast(float, expiry) <= now:
                    raise ValueError("cannot issue an expired enrollment")
            apply_enrollment_event(StoredEvent(0, now, kind, payload), candidate)

        def persist(_result: None) -> None:
            self._journal.append(kind, payload, durable=True)

        await self._hub.state_mutations.run(
            self._hub.state,
            mutate,
            persist=persist,
        )
