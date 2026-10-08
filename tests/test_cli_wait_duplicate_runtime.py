# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI mailbox replay duplication qualification
"""Duplicate and reorder actual hub frames through an owned WebSocket fault relay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cli_e2e_helpers import CliResult, run_cli
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mailbox_cursor import cursor_path, load_cursor


@contextlib.asynccontextmanager
async def duplicate_chat_relay(
    uri: str, seat: str, sequence: str
) -> AsyncIterator[tuple[str, list[int]]]:
    """Forward actual protocol traffic while repeating a reordered three-chat burst."""
    observed: list[int] = []

    async def handler(client: ServerConnection) -> None:
        """Join both owned directions when the actual CLI or hub disconnects."""
        with contextlib.suppress(ConnectionClosed):
            async with connect(uri) as upstream:

                async def inbound() -> None:
                    """Forward real registration, acknowledgements and heartbeats unchanged."""
                    async for raw in client:
                        await upstream.send(raw)

                async def outbound() -> None:
                    """Repeat genuine chat events, optionally removing legacy sequence metadata."""
                    pending: list[dict[str, object]] = []
                    selected: set[int] = set()
                    async for raw in upstream:
                        frame: dict[str, object] = json.loads(raw)
                        if frame.get("type") != "chat" or frame.get("target") != seat:
                            await client.send(raw)
                            continue
                        seq = frame["seq"]
                        assert isinstance(seq, int) and not isinstance(seq, bool) and seq > 0
                        if seq in selected:
                            continue
                        selected.add(seq)
                        observed.append(seq)
                        pending.append(frame)
                        if len(pending) < 3:
                            continue
                        for message in (pending[2], pending[0], pending[1]):
                            if sequence == "missing":
                                message.pop("seq")
                            elif sequence == "boolean":
                                message["seq"] = True
                            elif sequence == "zero":
                                message["seq"] = 0
                            encoded = json.dumps(message)
                            await client.send(encoded)
                            await client.send(encoded)
                        pending.clear()

                tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
                try:
                    done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async with serve(handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", observed


@pytest.mark.parametrize(
    ("mailbox", "sequence"),
    [(True, "valid"), (False, "valid"), (True, "missing"), (True, "boolean"), (True, "zero")],
)
async def test_cli_arm_surfaces_distinct_events_without_repeating_durable_rows(
    tmp_path: Path, mailbox: bool, sequence: str
) -> None:
    """Run the actual CLI against live repeated events and verify its persisted cursor."""
    marker = uuid.uuid4().hex
    seat = f"CLI-DUP/recipient-{marker}"
    receiver = f"{seat}-rx"
    cursor = cursor_path(seat)
    repeated, last = f"equal-payload-{marker}", f"last-payload-{marker}"
    command: asyncio.Task[CliResult] | None = None
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (hub, uri):
            async with duplicate_chat_relay(uri, seat, sequence) as (relay, observed):
                command = asyncio.create_task(
                    asyncio.to_thread(
                        run_cli,
                        "arm",
                        "--name",
                        receiver,
                        "--for",
                        seat,
                        "--role",
                        seat,
                        "--mailbox" if mailbox else "--no-mailbox",
                        "--max-wakes",
                        "1",
                        "--wake-jitter",
                        "0",
                        uri=relay,
                        timeout=10,
                        cwd=tmp_path,
                    )
                )
                deadline = asyncio.get_running_loop().time() + 5
                while receiver not in hub.online_agents():
                    assert not command.done(), "CLI exited before establishing its real receiver"
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                sender = await connect_agent(f"peer-{marker}", uri)
                try:
                    for payload in (repeated, repeated, last):
                        message_id = uuid.uuid4().hex
                        await sender.agent.send_message(
                            "chat",
                            target=receiver,
                            payload=payload,
                            client_msg_id=message_id,
                            receipt_requested=True,
                        )

                        def delivered(frame: dict[str, object], expected: str = message_id) -> bool:
                            """Match the real receipt of this just-dispatched journal event."""
                            return (
                                frame.get("client_msg_id") == expected
                                and frame.get("type") == "delivery_receipt"
                            )

                        verdict = await sender.recorder.wait_for(delivered)
                        assert verdict["delivered"] is True
                finally:
                    await close_agents(sender)
                result = await command
                assert result.returncode == 0, result.stderr
                assert len(observed) == 3 and len(set(observed)) == 3
                copies = 1 if mailbox and sequence == "valid" else 2
                assert result.stdout.count(repeated) == 2 * copies
                assert result.stdout.count(last) == copies
                if mailbox:
                    assert load_cursor(cursor) == (max(observed) if sequence == "valid" else 0)
                else:
                    assert not cursor.exists()
    finally:
        if command is not None and not command.done():
            await asyncio.gather(command, return_exceptions=True)
        cursor.unlink(missing_ok=True)
        store.close()
