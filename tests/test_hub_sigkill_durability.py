# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what a hub acknowledged survives kill -9
"""Kill a real ``synapse hub`` process with SIGKILL and check what survives.

One hub process with a durable journal acknowledges a keyed claim, a
directed chat to an offline recipient that asks for a delivery receipt, and a
progress note. The process is then killed with SIGKILL, with no shutdown of
any kind, and a second process opens the same database. Every acknowledged
effect must still be there: the claim with its epoch, the chat in the
recipient's mailbox, the pending receipt, which the recipient's ack settles
for the sender, and the note. A retry of the keyed claim must return the
first grant rather than apply again.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import _CLI, _await_listening, free_port
from synapse_channel import MessageType, SynapseAgent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGKILL")

TASK = "DURABLE-1"
PATH = "src/durable.py"


@contextlib.contextmanager
def _hub_process(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[str], str]]:
    """Start ``synapse hub`` on the test database; always reap the process."""
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [
            *_CLI,
            "hub",
            "--port",
            str(port),
            "--db",
            str(tmp_path / "durable.db"),
            "--identity-pins",
            str(tmp_path / "pins.json"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _await_listening(port)
        yield proc, f"ws://127.0.0.1:{port}"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@contextlib.asynccontextmanager
async def _agent(
    name: str, uri: str, *, mailbox: bool = False
) -> AsyncIterator[tuple[SynapseAgent, asyncio.Queue[dict[str, Any]]]]:
    frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def take(frame: dict[str, Any]) -> None:
        frames.put_nowait(frame)

    agent = SynapseAgent(
        name, take, uri=uri, verbose=False, machine_identity=False, mailbox=mailbox
    )
    task = asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(8.0)
        yield agent, frames
    finally:
        agent.running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _next(
    frames: asyncio.Queue[dict[str, Any]], match: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    while True:
        frame = await asyncio.wait_for(frames.get(), 8.0)
        assert frame.get("type") != MessageType.ERROR, frame.get("payload")
        if match(frame):
            return frame


def _is(msg_type: str, **fields: Any) -> Callable[[dict[str, Any]], bool]:
    return lambda frame: (
        frame.get("type") == msg_type
        and all(frame.get(key) == value for key, value in fields.items())
    )


async def test_every_acknowledged_effect_survives_kill_9(tmp_path: Path) -> None:
    with _hub_process(tmp_path) as (first, uri):
        async with _agent("P/writer", uri) as (writer, frames):
            await writer.claim(TASK, note="durable", ttl_seconds=3600, paths=[PATH], idem_key="k-1")
            granted = await _next(frames, _is(MessageType.CLAIM_GRANTED, task_id=TASK))
            await writer.send_message(
                MessageType.CHAT,
                target="P/reader",
                payload="kept for the reader",
                receipt_requested=True,
                client_msg_id="m-1",
            )
            immediate = await _next(frames, _is(MessageType.DELIVERY_RECEIPT, client_msg_id="m-1"))
            await writer.post_progress(TASK, "half done", idem_key="p-1")
            noted = await _next(frames, _is(MessageType.LEDGER_PROGRESS_POSTED))
        # Acknowledged; now the process dies with no chance to flush or anchor.
        os.kill(first.pid, signal.SIGKILL)
        assert first.wait(timeout=5) == -signal.SIGKILL

    with _hub_process(tmp_path) as (_second, uri):
        async with _agent("P/writer", uri) as (writer, frames):
            await writer.request_state()
            snapshot = await _next(frames, _is(MessageType.STATE_SNAPSHOT))
            await writer.claim(TASK, note="durable", ttl_seconds=3600, paths=[PATH], idem_key="k-1")
            retried = await _next(frames, _is(MessageType.CLAIM_GRANTED, task_id=TASK))
            async with _agent("P/reader", uri, mailbox=True) as (reader, inbox):
                delivered = await _next(
                    inbox, _is(MessageType.CHAT, sender="P/writer", payload="kept for the reader")
                )
                await reader.send_message(MessageType.ACK, target="System", seq=delivered["seq"])
                deferred = await _next(
                    frames, _is(MessageType.DELIVERY_RECEIPT, client_msg_id="m-1", deferred=True)
                )

    claims = {claim["task_id"]: claim for claim in snapshot["snapshot"]["active_claims"]}
    assert claims[TASK]["owner"] == "P/writer"
    assert claims[TASK]["epoch"] == granted["epoch"]
    assert retried["epoch"] == granted["epoch"]
    assert delivered["target"] == "P/reader"
    assert (immediate["delivered"], immediate["dead_lettered"]) == (False, True)
    assert (deferred["delivered"], deferred["ack_receiver"]) == (True, "P/reader")
    assert noted["note"]["task_id"] == TASK
