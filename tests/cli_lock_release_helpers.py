# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real lock reply forwarding fixture
"""Forward real hub frames with bounded reply-loss and delay controls."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.protocol import MessageType


@contextlib.asynccontextmanager
async def release_reply_proxy(
    uri: str,
    *,
    drop_confirmation: bool,
    grant_delay: float = 0,
    inject_foreign: bool = False,
) -> AsyncIterator[tuple[str, dict[str, bool | int]]]:
    """Drop selected real hub replies while forwarding actual mutations unchanged."""
    fault: dict[str, bool | int] = {
        "active": True,
        "claim_denied": False,
        "release_seen": False,
        "release_dispatches": 0,
        "confirmation_queries": 0,
    }

    async def bridge(client: ServerConnection) -> None:
        """Relay the actual connection and join both forwarding tasks on disconnect."""
        async with connect(uri) as upstream:

            async def inbound() -> None:
                async for raw in client:
                    frame = json.loads(raw)
                    if frame.get("type") == MessageType.RELEASE:
                        fault["release_dispatches"] = int(fault["release_dispatches"]) + 1
                    if "release_confirmation" in frame:
                        fault["confirmation_queries"] = int(fault["confirmation_queries"]) + 1
                    await upstream.send(raw)

            async def outbound() -> None:
                async for raw in upstream:
                    frame = json.loads(raw)
                    if inject_foreign and frame.get("type") in (
                        MessageType.CLAIM_GRANTED,
                        MessageType.RELEASE_GRANTED,
                    ):
                        for change in ({"task_id": "other-task"}, {"owner": "another-owner"}):
                            await client.send(json.dumps({**frame, **change}))
                        if frame.get("type") == MessageType.RELEASE_GRANTED:
                            for change in (
                                {"hub_id": "another-hub"},
                                {"sender": "another-peer"},
                                {"release_operation_id": "another-operation"},
                            ):
                                await client.send(json.dumps({**frame, **change}))
                    if frame.get("type") == MessageType.CLAIM_DENIED:
                        fault["claim_denied"] = True
                    if frame.get("type") == MessageType.RELEASE_GRANTED:
                        fault["release_seen"] = True
                        if grant_delay:
                            await asyncio.sleep(grant_delay)
                    if fault["active"] and (
                        frame.get("type") == MessageType.RELEASE_GRANTED
                        or (drop_confirmation and "release_confirmation" in frame)
                    ):
                        continue
                    await client.send(raw)

            tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def handler(client: ServerConnection) -> None:
        with contextlib.suppress(ConnectionClosed):
            await bridge(client)

    async with serve(handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", fault
