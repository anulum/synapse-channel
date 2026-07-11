# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed first-run coordination demo
"""Reusable installed demo routines for first-run Synapse validation.

The public examples in ``examples/`` are useful from a source checkout, but an
installed wheel also needs a self-contained success path. This module provides
the coordination demo behind ``synapse demo`` and keeps the example script wired
to the same tested implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from synapse_channel import SynapseAgent, SynapseHub


class DemoInbox:
    """Record received messages and wait for predicate matches during demos."""

    def __init__(self) -> None:
        """Create an empty in-memory inbox."""
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        """Append one received hub message to the inbox."""
        self.messages.append(data)

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        start: int = 0,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Return the first message at index ``start`` or later that matches.

        Parameters
        ----------
        predicate : Callable[[dict[str, Any]], bool]
            Filter used to choose the expected message.
        start : int, optional
            First inbox index considered by the wait. Defaults to ``0``.
        timeout : float, optional
            Maximum seconds to wait before raising ``TimeoutError``.

        Returns
        -------
        dict[str, Any]
            The matching message payload.

        Raises
        ------
        TimeoutError
            If no matching message arrives before ``timeout`` expires.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages[start:]):
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
    while loop.time() < deadline:
        try:
            connection = await ws_connect(f"ws://localhost:{port}", open_timeout=1.0)
        except (OSError, TimeoutError, asyncio.TimeoutError):
            await asyncio.sleep(0.02)
            continue
        await connection.close()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def _board_ready(agent: SynapseAgent, inbox: DemoInbox) -> list[str]:
    """Request the board and return its current ready set from a fresh snapshot."""
    start = len(inbox.messages)
    await agent.request_board()
    snap = await inbox.wait_for(lambda m: m.get("type") == "board_snapshot", start=start)
    return list(snap["board"].get("ready", []))


async def run_coordination_demo(port: int) -> list[str]:
    """Drive one task through the coordination plane and return narration lines.

    The demo starts an in-process hub, connects a planner and worker, declares a
    dependent plan, verifies file-scope claim exclusion, completes the blocked
    dependency, and hands the unblocked task to another agent.

    Parameters
    ----------
    port : int
        Local TCP port the in-process hub should listen on.

    Returns
    -------
    list[str]
        Human-readable narration lines in execution order.
    """
    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        print(line)

    hub = SynapseHub(hub_id="demo-hub")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    planner_rx, worker_rx = DemoInbox(), DemoInbox()
    planner = SynapseAgent("PLANNER", planner_rx, uri=uri, verbose=False)
    worker = SynapseAgent("WORKER", worker_rx, uri=uri, verbose=False)
    conns: list[asyncio.Task[None]] = []

    try:
        await _await_listening(port)
        conns = [asyncio.create_task(planner.connect()), asyncio.create_task(worker.connect())]
        await planner.wait_until_ready(3.0)
        await worker.wait_until_ready(3.0)
        say("• Two agents are online: PLANNER and WORKER.")

        await planner.post_task("BUILD", title="Compile the package")
        await planner.post_task("TEST", title="Run the suite", depends_on=["BUILD"])
        await worker_rx.wait_for(
            lambda m: (
                m.get("type") == "ledger_task_posted" and m.get("task", {}).get("task_id") == "TEST"
            )
        )
        say("• PLANNER declared BUILD and TEST (TEST depends on BUILD).")

        say(f"• Board ready set: {await _board_ready(planner, planner_rx)}  (TEST waits on BUILD)")

        await worker.claim("BUILD", note="starting", paths=["src"])
        await planner_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "BUILD"
        )
        say("• WORKER claimed BUILD with a file scope over src/.")

        await planner.claim("EDIT", paths=["src/app.py"])
        denied = await planner_rx.wait_for(lambda m: m.get("type") == "claim_denied")
        say(f"• PLANNER's overlapping claim on src/app.py was refused: {denied['payload']}")

        await worker.save_checkpoint("BUILD", "artifact=dist/pkg.whl")
        await worker.update_ledger_task("BUILD", status="done")
        await worker_rx.wait_for(
            lambda m: (
                m.get("type") == "ledger_task_updated"
                and m.get("task", {}).get("task_id") == "BUILD"
            )
        )
        say(
            f"• WORKER finished BUILD (checkpoint saved); board ready set: "
            f"{await _board_ready(planner, planner_rx)}"
        )

        await worker.claim("TEST")
        await planner_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "TEST"
        )
        await worker.handoff("TEST", "PLANNER", note="over to you")
        await planner_rx.wait_for(
            lambda m: m.get("type") == "handoff_granted" and m.get("task_id") == "TEST"
        )
        say("• WORKER handed TEST off to PLANNER with no release/re-claim gap.")
        return log
    finally:
        planner.running = False
        worker.running = False
        for task in (*conns, server):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def run_installed_demo() -> list[str]:
    """Run the first-run coordination demo on a free local port."""
    return asyncio.run(run_coordination_demo(_free_port()))
