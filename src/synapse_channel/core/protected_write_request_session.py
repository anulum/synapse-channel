# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — authenticated protected request sessions
"""Bind raw protected requests to one live enrollment inside the authority."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping

from synapse_channel.core.message_auth import MessageReplayCache
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_refusal import ProtectedWriteRefusalError
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    authenticate_protected_request,
    recheck_authenticated_protected_request,
)


class ProtectedWriteRequestSession:
    """Authenticate sequential requests without trusting transport assertions.

    Parameters
    ----------
    limits:
        Authority-owned representation and proposal limits.
    current_enrollments:
        Live registry shared with the authority's mutation actor.
    replay_cache:
        Shared cache backed by the transport's durable nonce ledger.
    clock:
        Trusted authority clock in Unix seconds.
    dispatch:
        Existing actor-bound protected handlers, never a frontend callback.
    reason_codes:
        Enrolled response vocabulary.

    Notes
    -----
    Obtain sessions from ``ProtectedWriteTransport.open_session``. A failed or
    concurrent exchange invalidates the session; an admitted operation may be
    durable despite an absent response. Inspect its original journal identity.
    """

    def __init__(
        self,
        *,
        limits: ProtectedWriteProposalLimits,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        replay_cache: MessageReplayCache,
        clock: Callable[[], float],
        dispatch: Callable[[AuthenticatedProtectedRequest], Awaitable[bytes]],
        reason_codes: frozenset[str],
    ) -> None:
        self._limits = limits
        self._current_enrollments = current_enrollments
        self._replay = replay_cache
        self._clock = clock
        self._dispatch = dispatch
        self._reason_codes = reason_codes
        self._bound: ProtectedSessionEnrollment | None = None
        self._busy = False
        self._closed = False

    async def exchange(self, raw: str | bytes) -> bytes:
        """Verify original signed bytes and return a request-bound authority result.

        Parameters
        ----------
        raw:
            Original client JSON, independently decoded here before dispatch.

        Returns
        -------
        bytes
            Canonical result bytes. Clients still verify the authority signature.

        Raises
        ------
        ValueError
            For a refusal before dispatch, or an unavailable/concurrent session.
        ProtectedWriteRefusalError
            For an authority-owned synchronous check that proves the requested
            mutation never committed, including a rolled-back response builder.
        RuntimeError
            If handler invocation or subsequent validation fails otherwise with ValueError.
            Its operation may have committed; inspect the original journal
            identity instead of interpreting the failure as a request refusal.
        Exception
            When the existing authority handler fails; no result is fabricated.
        """
        if self._closed or self._busy:
            self._closed = True
            raise ValueError("protected request session unavailable")
        self._busy = True
        try:
            parsed = parse_protected_write_request(raw, limits=self._limits)
            frame = json.loads(parsed.canonical_bytes)
            registry = self._current_enrollments()
            enrolled = registry.get(frame["session_id"])
            if enrolled is None or (self._bound is not None and self._bound != enrolled):
                raise ValueError("connection session unavailable or changed")
            authenticated = authenticate_protected_request(
                raw,
                limits=self._limits,
                enrollments=registry,
                authenticated_principal=enrolled.principal,
                replay_cache=self._replay,
                now=self._clock(),
            )
            self._bound = authenticated.enrollment
            try:
                response = await self._dispatch(authenticated)
            except ProtectedWriteRefusalError:
                raise
            except ValueError as exc:
                raise RuntimeError("protected authority outcome unavailable") from exc
            try:
                recheck_authenticated_protected_request(
                    authenticated,
                    enrollments=self._current_enrollments(),
                    authenticated_principal=enrolled.principal,
                    now=self._clock(),
                )
                if self._closed or type(response) is not bytes:
                    raise ValueError("authority response unavailable")
                return parse_protected_write_result(
                    response,
                    request=authenticated.parsed.canonical_bytes,
                    limits=self._limits,
                    reason_codes=self._reason_codes,
                ).canonical_bytes
            except ValueError as exc:
                raise RuntimeError("protected authority outcome unavailable") from exc
        except BaseException:
            self._closed = True
            raise
        finally:
            self._busy = False
