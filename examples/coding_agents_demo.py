# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — two coding agents editing one repository without collisions
"""Two agents work the same repo in parallel; the hub keeps them off each other.

Run it with no arguments::

    python examples/coding_agents_demo.py

`api-dev` leases the API source, `test-dev` leases the tests. When `test-dev`
reaches into the API's file scope the hub refuses the claim, so the two never
edit the same files; a disjoint claim is granted. `api-dev` then tells `test-dev`
directly that the API is ready, and `test-dev` reads that message. The logic is
in :func:`run_demo`, which the test-suite drives against an ephemeral port.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from typing import Any

from synapse_channel import SynapseAgent, SynapseHub

logging.getLogger("websockets.server").setLevel(logging.CRITICAL)


def _free_port() -> int:
    """Reserve and immediately release an ephemeral localhost port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _Inbox:
    """Records every message a client receives and waits for a predicate."""

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


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    """Block until ``port`` accepts a TCP connection or the timeout elapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.open_connection("localhost", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def run_demo(port: int) -> list[str]:
    """Run two coding agents through a no-collision edit session and narrate it.

    Parameters
    ----------
    port : int
        Port the in-process hub listens on.

    Returns
    -------
    list[str]
        The narration lines, in order, so a test can assert the sequence.
    """
    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        print(line)

    hub = SynapseHub(hub_id="repo-hub")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    api_rx, test_rx = _Inbox(), _Inbox()
    api = SynapseAgent("api-dev", api_rx, uri=uri, verbose=False)
    test = SynapseAgent("test-dev", test_rx, uri=uri, verbose=False)
    conns: list[asyncio.Task[None]] = []

    try:
        await _await_listening(port)
        conns = [asyncio.create_task(api.connect()), asyncio.create_task(test.connect())]
        await api.wait_until_ready(3.0)
        await test.wait_until_ready(3.0)
        say("• Two coding agents are on the repo: api-dev and test-dev.")

        # api-dev leases the API source files.
        await api.claim("edit-api", note="implementing", paths=["src/app/api.py"])
        await test_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "edit-api"
        )
        say("• api-dev claimed src/app/api.py.")

        # test-dev reaches into the API's scope — the hub refuses the overlap.
        await test.claim("touch-api", paths=["src/app/api.py"])
        denied = await test_rx.wait_for(lambda m: m.get("type") == "claim_denied")
        say(f"• test-dev's claim on the same file was refused: {denied['payload']}")

        # A disjoint claim is fine — the two work in parallel without collision.
        await test.claim("edit-tests", paths=["tests/test_api.py"])
        await api_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "edit-tests"
        )
        say("• test-dev claimed tests/test_api.py — disjoint scope, granted.")

        # api-dev finishes and tells test-dev directly, then releases the lease.
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
    """Run the demo on a free port for interactive use."""
    print("=== SYNAPSE CHANNEL — coding agents, no collisions ===")
    asyncio.run(run_demo(_free_port()))
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
