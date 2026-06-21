# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — runnable end-to-end coordination demo
"""A narrated walk through the coordination plane against a live in-process hub.

Run it with no arguments::

    python examples/coordination_demo.py

It starts a hub, connects a planner and a worker, and drives one full task
through the bus: declare on the blackboard, watch a dependent task stay blocked,
claim with a file scope, reject an overlapping claim, finish the plan task so the
dependent unblocks, and hand that task off. Every step prints what happened, so
the script doubles as a read-along tour. The logic lives in :func:`run_demo`,
which the test-suite drives against an ephemeral port; :func:`main` picks a free
port for interactive use.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from typing import Any

from synapse_channel import SynapseAgent, SynapseHub

# The readiness probe opens a bare TCP socket, which the WS server logs as a
# failed handshake; silence that so the narration reads cleanly.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)


def _free_port() -> int:
    """Reserve and immediately release an ephemeral localhost port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _Inbox:
    """Records every message a client receives and waits for predicates."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        self.messages.append(data)

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        start: int = 0,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Return the first message at index >= ``start`` matching ``predicate``."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages[start:]):
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


async def _board_ready(agent: SynapseAgent, inbox: _Inbox) -> list[str]:
    """Request the board and return its current ready set (only fresh snapshots)."""
    start = len(inbox.messages)
    await agent.request_board()
    snap = await inbox.wait_for(lambda m: m.get("type") == "board_snapshot", start=start)
    return list(snap["board"].get("ready", []))


async def run_demo(port: int) -> list[str]:
    """Drive one task through the whole coordination plane and narrate it.

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

    hub = SynapseHub(hub_id="demo-hub")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"

    planner_rx, worker_rx = _Inbox(), _Inbox()
    planner = SynapseAgent("PLANNER", planner_rx, uri=uri, verbose=False)
    worker = SynapseAgent("WORKER", worker_rx, uri=uri, verbose=False)
    conns: list[asyncio.Task[None]] = []

    try:
        await _await_listening(port)
        conns = [asyncio.create_task(planner.connect()), asyncio.create_task(worker.connect())]
        await planner.wait_until_ready(3.0)
        await worker.wait_until_ready(3.0)
        say("• Two agents are online: PLANNER and WORKER.")

        # 1. PLANNER declares a plan: TEST depends on BUILD.
        await planner.post_task("BUILD", title="Compile the package")
        await planner.post_task("TEST", title="Run the suite", depends_on=["BUILD"])
        await worker_rx.wait_for(
            lambda m: (
                m.get("type") == "ledger_task_posted" and m.get("task", {}).get("task_id") == "TEST"
            )
        )
        say("• PLANNER declared BUILD and TEST (TEST depends on BUILD).")

        # 2. The board shows BUILD ready while TEST stays blocked on its dependency.
        say(f"• Board ready set: {await _board_ready(planner, planner_rx)}  (TEST waits on BUILD)")

        # 3. WORKER leases BUILD and locks the src/ subtree.
        await worker.claim("BUILD", note="starting", paths=["src"])
        await planner_rx.wait_for(
            lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "BUILD"
        )
        say("• WORKER claimed BUILD with a file scope over src/.")

        # 4. PLANNER tries to grab a file inside that scope — the hub refuses.
        await planner.claim("EDIT", paths=["src/app.py"])
        denied = await planner_rx.wait_for(lambda m: m.get("type") == "claim_denied")
        say(f"• PLANNER's overlapping claim on src/app.py was refused: {denied['payload']}")

        # 5. WORKER checkpoints and finishes BUILD on the plan; TEST unblocks.
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

        # 6. WORKER claims the now-ready TEST and hands it to PLANNER in one step.
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


def main() -> int:
    """Run the demo on a free port for interactive use."""
    print("=== SYNAPSE CHANNEL — coordination demo ===")
    asyncio.run(run_demo(_free_port()))
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
