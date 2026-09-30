# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real large-board receive and dashboard journeys
"""Exercise finite client receive bounds through actual hub and HTTP sockets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from websockets.frames import Close

from cli_e2e_helpers import run_cli
from dashboard_helpers import _authorized_get
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_lifecycle import MAX_HUB_MESSAGE_BYTES, _received_close
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION, MessageType
from synapse_channel.dashboard import fetch_dashboard_snapshot, start_dashboard_server

TASK_COUNT = 40
DESCRIPTION = "board evidence " + "x" * 32768


async def _seed_large_board(owner: AgentHandle) -> None:
    """Declare a board exceeding one MiB through public, acknowledged task verbs."""
    for index in range(TASK_COUNT):
        task_id = f"large-board-{index}"
        await owner.agent.post_task(task_id, f"Task {index}", description=DESCRIPTION)

        def posted(message: dict[str, Any], expected_id: str = task_id) -> bool:
            """Match the current declaration before sending the next task."""
            return (
                message.get("type") == MessageType.LEDGER_TASK_POSTED
                and message.get("task", {}).get("task_id") == expected_id
            )

        await owner.recorder.wait_for(posted)


async def test_large_board_reaches_native_client_and_dashboard() -> None:
    """A real hub board above the old receive limit reaches both public readers."""
    async with running_hub() as (_hub, uri):
        owner = await connect_agent("large-board-owner", uri)
        try:
            await _seed_large_board(owner)
            await owner.agent.request_board()
            frame = await owner.recorder.wait_for(
                lambda message: message.get("type") == MessageType.BOARD_SNAPSHOT
            )
            assert 1024 * 1024 < len(json.dumps(frame).encode()) < MAX_HUB_MESSAGE_BYTES
            assert len(frame["board"]["tasks"]) == TASK_COUNT
            assert owner.agent.running
            snapshot = await fetch_dashboard_snapshot(
                uri=uri,
                name="large-dashboard-reader",
                token=None,
                ready_timeout=3,
                response_timeout=3,
            )
            assert len(snapshot.board["tasks"]) == TASK_COUNT
            assert "large-board-owner" in snapshot.online_agents
        finally:
            await close_agents(owner)


async def test_large_board_reaches_packaged_cli_and_authenticated_http(tmp_path: Path) -> None:
    """The actual CLI and dashboard HTTP entrypoints preserve a large task board."""
    async with running_hub() as (_hub, uri):
        owner = await connect_agent("large-entrypoint-owner", uri)
        try:
            await _seed_large_board(owner)
            result = await asyncio.to_thread(
                run_cli, "board", "--name", "large-cli-reader", uri=uri, cwd=tmp_path
            )
            assert result.returncode == 0, result.output[:512]
            assert f"Tasks ({TASK_COUNT})" in result.stdout
            assert all(f"large-board-{index}" in result.stdout for index in range(TASK_COUNT))
            server = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="large-http-reader",
                token=None,
                ready_timeout=3,
                response_timeout=3,
                refresh_seconds=5,
                allow_non_loopback=False,
            )
            try:
                status, content_type, body = await asyncio.to_thread(
                    _authorized_get, server, "/snapshot.json"
                )
                assert status == 200, body[:512]
                assert content_type == "application/json"
                document = json.loads(body)
                assert len(document["board"]["tasks"]) == TASK_COUNT
                assert "large-entrypoint-owner" in document["online_agents"]
            finally:
                await asyncio.to_thread(server.close)
        finally:
            await close_agents(owner)


async def test_oversize_hub_response_closes_the_native_client() -> None:
    """An actual oversized response still triggers the finite WebSocket bound."""

    async def oversized_peer(socket: ServerConnection) -> None:
        """Welcome a real client, then send one response beyond its size bound."""
        await socket.recv()
        await socket.send(
            json.dumps(
                {
                    "type": MessageType.WELCOME,
                    "hub_id": "size-bound-test",
                    "protocol_version": WIRE_PROTOCOL_VERSION,
                }
            )
        )
        try:
            await socket.send(
                json.dumps(
                    {
                        "type": MessageType.CHAT,
                        "sender": "SynapseHub",
                        "payload": "x" * (MAX_HUB_MESSAGE_BYTES + 1),
                    }
                )
            )
            await socket.recv()
        except ConnectionClosed:
            return

    async with serve(oversized_peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = SynapseAgent("size-bound-reader", uri=f"ws://127.0.0.1:{port}", verbose=False)
        listener = asyncio.create_task(client.connect())
        try:
            await asyncio.wait_for(listener, 5)
            assert not client.running
            assert not client.ready_event.is_set()
            assert client.last_close_code == 1009
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


def test_local_receive_refusal_has_a_stable_diagnostic() -> None:
    """A size refusal without a peer close frame remains actionable and authored."""
    error = ConnectionClosedError(None, Close(1009, "library-dependent size detail"))
    assert _received_close(error) == (1009, "received hub message exceeds the client size limit")
