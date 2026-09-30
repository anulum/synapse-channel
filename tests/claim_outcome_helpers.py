# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live claim response fault injection
"""Lose or corrupt real hub replies without replacing the hub or its clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed


@dataclass
class ClaimProxy:
    """Observe mutations and inject transport faults into an actual WebSocket."""

    upstream: str
    drop_grants: bool = True
    drop_snapshots: bool = False
    grant_delay: float = 0.0
    transform: Callable[[dict[str, Any]], None] | None = None
    requests: list[str] = field(default_factory=list)

    async def handle(self, downstream: ServerConnection) -> None:
        """Forward both directions and join all connection tasks on exit."""
        async with connect(self.upstream, max_size=8 * 1024 * 1024) as upstream:

            async def requests() -> None:
                """Relay real client requests and count lease mutations."""
                async for raw in downstream:
                    data = json.loads(raw)
                    self.requests.append(str(data.get("type")))
                    await upstream.send(raw)

            async def replies() -> None:
                """Apply only the configured reply loss or corruption."""
                async for raw in upstream:
                    data = json.loads(raw)
                    if self.drop_grants and data.get("type") == "claim_granted":
                        continue
                    if self.grant_delay and data.get("type") == "claim_granted":
                        await asyncio.sleep(self.grant_delay)
                    if self.drop_snapshots and data.get("type") == "state_snapshot":
                        continue
                    if self.transform is not None:
                        self.transform(data)
                    await downstream.send(json.dumps(data))

            tasks = [asyncio.create_task(requests()), asyncio.create_task(replies())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, (asyncio.CancelledError, ConnectionClosed)
                    ):
                        raise result


@contextlib.asynccontextmanager
async def claim_proxy(proxy: ClaimProxy) -> AsyncIterator[str]:
    """Expose one real TCP proxy bound to an OS-selected local port."""
    async with serve(proxy.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"
