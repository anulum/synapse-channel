# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated protected wire ingress
"""Opt-in protected WebSocket ingress; no default hub dispatch registration.

The operator supplies an isolated listener, protected credentials and real
authority handlers. This class neither creates services nor executes writes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Mapping

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.message_auth import MessageReplayCache
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protected_write_proposal import ProtectedWriteProposalLimits
from synapse_channel.core.protected_write_request import parse_protected_write_request
from synapse_channel.core.protected_write_result import parse_protected_write_result
from synapse_channel.core.protected_write_session_auth import (
    AuthenticatedProtectedRequest,
    ProtectedSessionEnrollment,
    authenticate_protected_request,
    recheck_authenticated_protected_request,
)


class ProtectedWriteTransport:
    """Bind each socket to a verified session and authenticate every request.

    Parameters
    ----------
    limits:
        Explicit wire representation bounds; also configure listener max_size.
    current_enrollments:
        Current operator-owned registry, not connection-time snapshots.
    replay_store:
        Open file-backed nonce ledger retained across listener restarts.
    sequence_floor_mode:
        Explicit operator choice; never silently enable strict sequence floors.
    clock:
        Trusted server clock.
    dispatch:
        Real authority handler. Mutations must use the authenticated hub actor
        boundary; reads must enforce their own ordered scope and state checks.
    reason_codes:
        Enrolled response vocabulary.

    Notes
    -----
    Handler output is validated for representation and request binding, not
    cryptographically certified here. Clients still verify authority signatures.
    Registry updates and mutations must share authority ordering.
    """

    def __init__(
        self,
        *,
        limits: ProtectedWriteProposalLimits,
        current_enrollments: Callable[[], Mapping[str, ProtectedSessionEnrollment]],
        replay_store: DurableMessageAuthReplayStore,
        sequence_floor_mode: SequenceFloorMode,
        clock: Callable[[], float],
        dispatch: Callable[[AuthenticatedProtectedRequest], Awaitable[bytes]],
        reason_codes: frozenset[str],
    ) -> None:
        if replay_store.path == ":memory:" or replay_store.path.startswith("file:"):
            raise ValueError("protected ingress requires an explicit file-backed replay ledger")
        if not math.isfinite(replay_store.window_seconds) or replay_store.window_seconds <= 0:
            raise ValueError("finite positive replay retention required")
        if not isinstance(sequence_floor_mode, SequenceFloorMode):
            raise ValueError("explicit sequence floor mode required")
        if type(reason_codes) is not frozenset:
            raise ValueError("immutable response reason codes required")
        self._limits = limits
        self._current_enrollments = current_enrollments
        self._clock = clock
        self._dispatch = dispatch
        self._reason_codes = reason_codes
        self._replay = MessageReplayCache(
            window_seconds=replay_store.window_seconds,
            max_entries=replay_store.max_entries,
            durable=replay_store,
            sequence_floor_mode=sequence_floor_mode,
        )

    async def __call__(self, connection: ServerConnection) -> None:
        """Handle one socket without exposing request bodies or error details.

        Parameters
        ----------
        connection:
            Accepted socket on the separately isolated, bounded listener.
        """
        bound: ProtectedSessionEnrollment | None = None
        try:
            async for raw in connection:
                parsed = parse_protected_write_request(raw, limits=self._limits)
                frame = json.loads(parsed.canonical_bytes)
                registry = self._current_enrollments()
                enrolled = registry.get(frame["session_id"])
                if enrolled is None or (bound is not None and bound != enrolled):
                    raise ValueError("connection session unavailable or changed")
                authenticated = authenticate_protected_request(
                    raw,
                    limits=self._limits,
                    enrollments=registry,
                    authenticated_principal=enrolled.principal,
                    replay_cache=self._replay,
                    now=self._clock(),
                )
                # Only a successfully verified signature can establish binding.
                bound = authenticated.enrollment
                response = await self._dispatch(authenticated)
                recheck_authenticated_protected_request(
                    authenticated,
                    enrollments=self._current_enrollments(),
                    authenticated_principal=bound.principal,
                    now=self._clock(),
                )
                if type(response) is not bytes:
                    raise ValueError("authority response must be immutable wire bytes")
                verified = parse_protected_write_result(
                    response,
                    request=authenticated.parsed.canonical_bytes,
                    limits=self._limits,
                    reason_codes=self._reason_codes,
                )
                await connection.send(verified.canonical_bytes)
        except ConnectionClosed:
            return
        except ValueError:
            await connection.close(code=1008, reason="protected request refused")
        except Exception:
            # A dispatch/storage failure is not a positive or effective receipt.
            # Exact recovery disposition remains in the authority journal.
            await connection.close(code=1011, reason="protected service unavailable")
