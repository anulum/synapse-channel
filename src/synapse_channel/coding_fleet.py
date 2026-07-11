# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaged coding-fleet demo runtime
"""Reusable coding-fleet demo runtime for examples and generated workspaces."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from synapse_channel import SynapseAgent, SynapseHub


class CodingFleetInbox:
    """Record demo messages and wait for expected hub events."""

    def __init__(self) -> None:
        """Create an empty inbox."""
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        """Append one received hub message."""
        self.messages.append(data)

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 3.0
    ) -> dict[str, Any]:
        """Return the first recorded message matching ``predicate``.

        Parameters
        ----------
        predicate : Callable[[dict[str, Any]], bool]
            Filter for the expected message.
        timeout : float, optional
            Maximum seconds to wait.

        Returns
        -------
        dict[str, Any]
            The matching message.

        Raises
        ------
        TimeoutError
            If no matching message arrives before ``timeout``.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages):
                if predicate(message):
                    return message
            await asyncio.sleep(0.01)
        raise TimeoutError("expected message did not arrive")


def _free_port() -> int:
    """Reserve and immediately release an ephemeral localhost TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    """Block until the hub accepts a WebSocket handshake or the timeout elapses.

    The probe completes one real WebSocket handshake and closes it cleanly
    instead of opening a bare TCP socket: an aborted TCP probe makes the hub's
    WebSocket server log ``opening handshake failed`` with a full traceback —
    and it records that abort after the probe returns, so no probe-scoped
    logger suppression can catch it. A clean handshake produces no error
    record at all, keeps a first run's stderr quiet without mutating any
    logger, and leaves genuine handshake errors visible.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        # Each handshake attempt is bounded by the caller's remaining budget so
        # a listener that accepts TCP but never answers the handshake cannot
        # stretch the probe past its deadline.
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            connection = await ws_connect(
                f"ws://localhost:{port}", open_timeout=min(1.0, remaining)
            )
        except (OSError, TimeoutError, asyncio.TimeoutError):
            await asyncio.sleep(0.02)
            continue
        await connection.close()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def run_coding_agents_demo(port: int) -> list[str]:
    """Run two coding agents through a no-collision edit session.

    Parameters
    ----------
    port : int
        Port the in-process hub listens on.

    Returns
    -------
    list[str]
        Narration lines in execution order.
    """
    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        print(line)

    hub = SynapseHub(hub_id="repo-hub")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    api_rx, test_rx = CodingFleetInbox(), CodingFleetInbox()
    api = SynapseAgent("api-dev", api_rx, uri=uri, verbose=False)
    test = SynapseAgent("test-dev", test_rx, uri=uri, verbose=False)
    conns: list[asyncio.Task[None]] = []

    try:
        await _await_listening(port)
        conns = [asyncio.create_task(api.connect()), asyncio.create_task(test.connect())]
        await api.wait_until_ready(3.0)
        await test.wait_until_ready(3.0)
        say("• Two coding agents are on the repo: api-dev and test-dev.")

        await api.claim("edit-api", note="implementing", paths=["src/app/api.py"])
        await test_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "edit-api"
        )
        say("• api-dev claimed src/app/api.py.")

        await test.claim("touch-api", paths=["src/app/api.py"])
        denied = await test_rx.wait_for(lambda m: m.get("type") == "claim_denied")
        say(f"• test-dev's claim on the same file was refused: {denied['payload']}")

        await test.claim("edit-tests", paths=["tests/test_api.py"])
        await api_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "edit-tests"
        )
        say("• test-dev claimed tests/test_api.py — disjoint scope, granted.")

        await api.chat(
            "API is ready on src/app/api.py — please update the tests", target="test-dev"
        )
        relayed = await test_rx.wait_for(
            lambda m: m.get("type") == "chat" and m.get("sender") == "api-dev"
        )
        say(f"• test-dev received: {relayed['payload']}")
        await api.release("edit-api")
        await test_rx.wait_for(
            lambda m: m.get("type") == "release_granted" and m.get("task_id") == "edit-api"
        )
        say("• api-dev released src/app/api.py — test-dev could now claim it if needed.")
        return log
    finally:
        api.running = False
        test.running = False
        for task in (*conns, server):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def main() -> int:
    """Run the coding-fleet demo on a free local port."""
    print("=== SYNAPSE CHANNEL — coding agents, no collisions ===")
    run_log = asyncio.run(run_coding_agents_demo(_free_port()))
    if not run_log:
        raise RuntimeError("coding fleet demo produced no narration")
    print("success: coding fleet demo completed")
    return 0
