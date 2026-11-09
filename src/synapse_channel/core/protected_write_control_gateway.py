# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — protected WebSocket frontend relay
"""Relay original protected frames without holding authority state or keys."""

from __future__ import annotations

import asyncio
from pathlib import Path

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.protected_write_control_client import open_protected_write_control
from synapse_channel.core.protected_write_control_policy import (
    ProtectedWriteControlPeer,
    ProtectedWriteControlPolicy,
)


class ProtectedWriteControlGateway:
    """Bridge each external connection to its own native authority control session.

    Parameters
    ----------
    path:
        Independently enrolled local control endpoint.
    authority_peer:
        Actual authority process identity supplied by the supervisor.
    policy:
        Same immutable continuity, incarnation and bounded policy as the authority.

    Notes
    -----
    The listener owner must bound WebSocket frame size, queue and connection
    capacity, stop accepts and drain handlers. The authority enforces its own
    independent private byte, concurrency, proof and current-state checks.
    Availability and plaintext confidentiality remain frontend-trusted. End
    clients must verify the existing request-bound authority signature; this
    frontend cannot certify its own response routing or client intent.
    """

    def __init__(
        self,
        path: Path,
        *,
        authority_peer: ProtectedWriteControlPeer,
        policy: ProtectedWriteControlPolicy,
    ) -> None:
        self._path = path
        self._peer = authority_peer
        self._policy = policy

    async def __call__(self, connection: ServerConnection) -> None:
        """Forward one client's exact frames and return only its bound responses.

        Parameters
        ----------
        connection:
            Actual accepted external WebSocket, owned by the caller's listener.
        """
        try:
            async with open_protected_write_control(
                self._path, authority_peer=self._peer, policy=self._policy
            ) as control:
                async for raw in connection:
                    async with asyncio.timeout(self._policy.timeout_seconds):
                        await connection.send(await control.exchange(raw))
        except ConnectionClosed:
            return
        except (ValueError, UnicodeError):
            await connection.close(code=1008, reason="protected request refused")
        except Exception:
            await connection.close(code=1011, reason="protected service unavailable")
