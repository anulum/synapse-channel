# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — runnable LLM-worker round-trip demo
"""Ask an on-channel model worker a question and print its reply.

Run it with no arguments::

    python examples/llm_team_demo.py

It starts a hub and one model worker in-process, then a USER client sends a
question and waits for the answer. If a local Ollama model is reachable the
worker uses it; otherwise it falls back to the deterministic offline backend, so
the demo runs anywhere. The logic lives in :func:`run_demo`, which the test-suite
drives with the offline backend; :func:`main` auto-detects Ollama for a real
answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import Any

from synapse_channel import SynapseAgent, SynapseHub, SynapseLLMWorker
from synapse_channel.client.launcher import detect_model

logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

QUESTION = "In one sentence, what problem does a coordination hub solve?"


def _free_port() -> int:
    """Reserve and immediately release an ephemeral localhost port."""
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
            _, writer = await asyncio.open_connection("localhost", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def run_demo(port: int, *, provider: str = "rule", model: str = "llama3") -> str:
    """Start a hub and a worker, ask one question, and return the reply text.

    Parameters
    ----------
    port : int
        Port the in-process hub listens on.
    provider : str, optional
        Worker backend: ``"rule"`` (offline, default) or ``"ollama"``.
    model : str, optional
        Model name for the ``ollama`` provider.

    Returns
    -------
    str
        The worker's reply payload.
    """
    hub = SynapseHub(hub_id="llm-demo")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"
    worker = SynapseLLMWorker(name="REASON", uri=uri, provider=provider, model=model)

    replies: list[dict[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == "chat" and data.get("sender") == "REASON":
            replies.append(data)

    user = SynapseAgent("USER", collect, uri=uri, verbose=False)
    tasks: list[asyncio.Task[None]] = []
    try:
        await _await_listening(port)
        tasks = [asyncio.create_task(worker.run()), asyncio.create_task(user.connect())]
        await user.wait_until_ready(3.0)
        await asyncio.sleep(0.4)  # let the worker register

        print(f"USER -> REASON: {QUESTION}")
        await user.chat(QUESTION, target="REASON")
        for _ in range(600):  # up to ~60s for a real model
            if replies:
                break
            await asyncio.sleep(0.1)
        reply = replies[-1]["payload"] if replies else "(no reply)"
        print(f"REASON: {reply}")
        return reply
    finally:
        user.running = False
        for task in (*tasks, server):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def main() -> int:
    """Run the demo, using a local Ollama model when one is reachable."""
    print("=== SYNAPSE CHANNEL — LLM worker demo ===")
    model = detect_model(["gemma3:1b", "gemma3", "llama3"])
    if model:
        print(f"(using Ollama model '{model}')")
        asyncio.run(run_demo(_free_port(), provider="ollama", model=model))
    else:
        print("(no Ollama model found — using the offline rule backend)")
        asyncio.run(run_demo(_free_port()))
    print("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
