# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from websockets.exceptions import ConnectionClosed

from synapse_channel.core.hub import (
    SynapseHub,
)


class FakeServerWS:
    """Stand-in for a hub-side server connection."""

    def __init__(
        self,
        incoming: list[str] | None = None,
        *,
        recv_blocks: bool = False,
        remote_address: tuple[str, int] = ("127.0.0.1", 54321),
    ) -> None:
        self.incoming = list(incoming or [])
        self.recv_blocks = recv_blocks
        self.remote_address = remote_address
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        # Used by the secured-hub auth handshake. When asked to block, never return,
        # so an ``asyncio.wait_for`` around it exercises the auth-timeout path.
        if self.recv_blocks:
            await asyncio.Event().wait()
        if not self.incoming:
            raise ConnectionClosed(None, None)
        return self.incoming.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def __aiter__(self) -> AsyncIterator[str]:
        while self.incoming:
            yield self.incoming.pop(0)

    def last(self) -> Any:
        return json.loads(self.sent[-1])

    def decoded(self) -> list[Any]:
        return [json.loads(raw) for raw in self.sent]


def _msg(**fields: Any) -> str:
    return json.dumps(fields)


def _hub() -> SynapseHub:
    return SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
