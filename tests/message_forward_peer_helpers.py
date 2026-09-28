# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scripted network peers for cross-hub forwarding tests
"""A real websocket server that answers a forward the way a legacy or hostile hub would.

A current hub always answers a well-formed forward correctly, so the answers that only an older
hub (which does not know the frame) or a faulty peer produces are served by this scripted peer
over a real loopback socket. The code under test uses its production transport unchanged.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

Reply = Callable[[dict[str, Any]], str | bytes | list[str] | None]
"""Builds the raw reply (or several, in order) to one received frame; ``None`` sends nothing."""


@dataclass
class ScriptedPeer:
    """A running scripted peer and every frame it received."""

    uri: str
    received: list[dict[str, Any]] = field(default_factory=list)


@contextlib.asynccontextmanager
async def scripted_peer(reply: Reply) -> AsyncIterator[ScriptedPeer]:
    """Serve ``reply`` on a free loopback port for the duration of the context.

    Parameters
    ----------
    reply : Reply
        Called with each decoded frame; its return value is sent back unchanged.

    Yields
    ------
    ScriptedPeer
        The peer's ``ws://`` URI and the frames it received.
    """
    peer = ScriptedPeer(uri="")

    async def handler(websocket: ServerConnection) -> None:
        async for raw in websocket:
            frame = json.loads(raw)
            peer.received.append(frame)
            answer = reply(frame)
            for message in answer if isinstance(answer, list) else [answer]:
                if message is not None:
                    await websocket.send(message)

    async with serve(handler, "localhost", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        peer.uri = f"ws://localhost:{port}"
        yield peer


def result_frame(forward_id: str, **fields: Any) -> str:
    """Return a ``multihub_message_result`` frame for ``forward_id`` with ``fields``."""
    frame: dict[str, Any] = {
        "type": "multihub_message_result",
        "sender": "SynapseHub",
        "forward_id": forward_id,
        "disposition": "accepted",
        "answering_hub": "scripted",
        "reason_code": "",
        "detail": "",
        "result": {},
    }
    frame.update(fields)
    return json.dumps(frame)


def error_frame(_frame: dict[str, Any]) -> str:
    """Answer like a hub that does not know the forward frame type."""
    return json.dumps({"type": "error", "sender": "SynapseHub", "payload": "Unknown message type"})
