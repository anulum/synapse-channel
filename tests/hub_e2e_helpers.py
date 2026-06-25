# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end hub test helpers

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast

from websockets.asyncio.client import ClientConnection

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("localhost", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


@contextlib.asynccontextmanager
async def running_hub(hub: SynapseHub | None = None) -> AsyncIterator[tuple[SynapseHub, str]]:
    actual = hub if hub is not None else SynapseHub(hub_id="syn-test")
    port = _free_port()
    task = asyncio.create_task(actual.serve("localhost", port))
    try:
        await _await_listening(port)
        yield actual, f"ws://localhost:{port}"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        self.messages.append(data)

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 3.0
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages):
                if predicate(message):
                    return message
            await asyncio.sleep(0.01)
        raise TimeoutError("expected message did not arrive")


@dataclass
class AgentHandle:
    agent: SynapseAgent
    recorder: Recorder
    task: asyncio.Task[None]

    async def close(self) -> None:
        self.agent.running = False
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


async def connect_agent(name: str, uri: str, *, wait_presence: bool = True) -> AgentHandle:
    recorder = Recorder()
    agent = SynapseAgent(name, recorder, uri=uri, heartbeat_interval=60.0, verbose=False)
    task = asyncio.create_task(agent.connect())
    handle = AgentHandle(agent=agent, recorder=recorder, task=task)
    if not await agent.wait_until_ready(3.0):
        await handle.close()
        raise TimeoutError(f"agent {name} did not receive hub welcome")
    if wait_presence:
        await recorder.wait_for(
            lambda m: m.get("type") == "presence_update" and m.get("agent") == name
        )
    return handle


async def close_agents(*handles: AgentHandle) -> None:
    for handle in reversed(handles):
        await handle.close()


async def read_json(websocket: ClientConnection, timeout: float = 3.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return cast(dict[str, Any], json.loads(raw))


async def read_until_type(
    websocket: ClientConnection, message_type: str, *, limit: int = 20, timeout: float = 3.0
) -> dict[str, Any]:
    for _ in range(limit):
        message = await read_json(websocket, timeout=timeout)
        if message.get("type") == message_type:
            return message
    raise TimeoutError(f"message type {message_type!r} did not arrive")


async def collect_available(
    websocket: ClientConnection, duration: float = 0.15
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + duration
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return messages
        try:
            messages.append(await read_json(websocket, timeout=remaining))
        except TimeoutError:
            return messages
