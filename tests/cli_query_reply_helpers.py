# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real lock reply forwarding fixture
"""Forward production hub frames while losing a selected query reply."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed


@dataclass
class ReplyTrace:
    """Actual forwarded requests, received replies and upstream connection count."""

    requests: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    connections: int = 0


@contextlib.asynccontextmanager
async def query_reply_proxy(
    uri: str, *, response_type: str, lose_reply: bool, close_code: int | None = None
) -> AsyncIterator[tuple[str, ReplyTrace]]:
    """Pass the real handshake and requests; optionally lose or close on a reply."""
    trace = ReplyTrace()

    async def bridge(client: ServerConnection) -> None:
        trace.connections += 1
        async with connect(uri) as upstream:

            async def inbound() -> None:
                async for raw in client:
                    frame = json.loads(raw)
                    trace.requests.append(str(frame.get("type")))
                    await upstream.send(raw)

            async def outbound() -> None:
                async for raw in upstream:
                    frame = json.loads(raw)
                    kind = str(frame.get("type"))
                    trace.replies.append(kind)
                    if kind == response_type:
                        if close_code is not None:
                            await client.close(code=close_code, reason="fixture reply loss")
                            return
                        if lose_reply:
                            continue
                    await client.send(raw)

            tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(client: ServerConnection) -> None:
        with contextlib.suppress(ConnectionClosed):
            await bridge(client)

    async with serve(handle, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", trace
