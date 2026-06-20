# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — in-process hub + real client end-to-end integration

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Callable
from typing import Any

from synapse_channel.client import SynapseAgent
from synapse_channel.hub import SynapseHub


def _free_port() -> int:
    """Reserve and release an ephemeral localhost port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    """Block until ``port`` accepts a TCP connection or the timeout elapses."""
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


class Recorder:
    """Collects every message a client receives for assertion helpers."""

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


async def test_hub_and_clients_full_roundtrip() -> None:
    port = _free_port()
    hub = SynapseHub(hub_id="syn-itest")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    alpha_rx = Recorder()
    beta_rx = Recorder()
    alpha = SynapseAgent("ALPHA", alpha_rx, uri=uri, verbose=False)
    beta = SynapseAgent("BETA", beta_rx, uri=uri, verbose=False)
    alpha_conn: asyncio.Task[None] | None = None
    beta_conn: asyncio.Task[None] | None = None

    try:
        await _await_listening(port)
        alpha_conn = asyncio.create_task(alpha.connect())
        beta_conn = asyncio.create_task(beta.connect())
        assert await alpha.wait_until_ready(3.0)
        assert await beta.wait_until_ready(3.0)
        assert alpha.hub_id == "syn-itest"

        # A claim by ALPHA is granted and broadcast to BETA.
        await alpha.claim("TASK-1", note="integration")
        granted = await beta_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "TASK-1"
        )
        assert granted["owner"] == "ALPHA"

        # A chat from BETA reaches ALPHA with a round-trip below one second.
        start = time.monotonic()
        await beta.chat("hello alpha", target="all")
        relayed = await alpha_rx.wait_for(
            lambda m: m.get("type") == "chat" and m.get("payload") == "hello alpha"
        )
        assert relayed["sender"] == "BETA"
        assert time.monotonic() - start < 1.0

        # The roster query returns both connected agents.
        await alpha.request_who()
        roster = await alpha_rx.wait_for(lambda m: m.get("type") == "who_snapshot")
        assert set(roster["online_agents"]) == {"ALPHA", "BETA"}

        # History records the relayed chat.
        await alpha.request_history(limit=10)
        history = await alpha_rx.wait_for(lambda m: m.get("type") == "history_snapshot")
        assert any(item.get("payload") == "hello alpha" for item in history["history"])

        # Releasing the task is broadcast as a grant-release.
        await alpha.release("TASK-1")
        await beta_rx.wait_for(
            lambda m: m.get("type") == "release_granted" and m.get("task_id") == "TASK-1"
        )
    finally:
        alpha.running = False
        beta.running = False
        for task in (alpha_conn, beta_conn, server):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def test_duplicate_name_is_rejected_end_to_end() -> None:
    port = _free_port()
    hub = SynapseHub()
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    first_rx = Recorder()
    second_rx = Recorder()
    first = SynapseAgent("DUP", first_rx, uri=uri, verbose=False)
    second = SynapseAgent("DUP", second_rx, uri=uri, verbose=False)
    first_conn: asyncio.Task[None] | None = None
    second_conn: asyncio.Task[None] | None = None

    try:
        await _await_listening(port)
        first_conn = asyncio.create_task(first.connect())
        assert await first.wait_until_ready(3.0)
        # Let the first registration settle so the name is taken.
        await first.chat("present", target="all")
        await asyncio.sleep(0.1)

        second_conn = asyncio.create_task(second.connect())
        assert await second.wait_until_ready(3.0)
        await second.chat("intruder", target="all")
        conflict = await second_rx.wait_for(lambda m: m.get("type") == "name_conflict")
        assert "already online" in conflict["payload"]
    finally:
        first.running = False
        second.running = False
        for task in (first_conn, second_conn, server):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def test_file_scope_overlap_is_rejected_end_to_end() -> None:
    port = _free_port()
    hub = SynapseHub()
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    alpha_rx = Recorder()
    beta_rx = Recorder()
    alpha = SynapseAgent("ALPHA", alpha_rx, uri=uri, verbose=False)
    beta = SynapseAgent("BETA", beta_rx, uri=uri, verbose=False)
    alpha_conn: asyncio.Task[None] | None = None
    beta_conn: asyncio.Task[None] | None = None

    try:
        await _await_listening(port)
        alpha_conn = asyncio.create_task(alpha.connect())
        beta_conn = asyncio.create_task(beta.connect())
        assert await alpha.wait_until_ready(3.0)
        assert await beta.wait_until_ready(3.0)

        # ALPHA owns the whole src/ subtree.
        await alpha.claim("EDIT-SRC", paths=["src"])
        await alpha_rx.wait_for(lambda m: m.get("type") == "claim_granted")

        # BETA tries to take a file inside src/ — must be refused.
        await beta.claim("EDIT-APP", paths=["src/app.py"])
        denied = await beta_rx.wait_for(lambda m: m.get("type") == "claim_denied")
        assert "file scope conflicts" in denied["payload"]

        # A disjoint claim by BETA is allowed.
        await beta.claim("EDIT-TESTS", paths=["tests"])
        await beta_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "EDIT-TESTS"
        )
    finally:
        alpha.running = False
        beta.running = False
        for task in (alpha_conn, beta_conn, server):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
