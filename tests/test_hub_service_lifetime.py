# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — unchanged direct serving and complete service teardown
"""Check real direct startup, checkpoint reopening and drain-before-close."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import closing
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import read_until_type, running_hub
from hub_service_lifetime_examples import until
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore
from synapse_channel.core.message_forward_transport import MessageForwardPeer, forward_message
from synapse_channel.core.message_forward_wire import MessageForwardRequest, MessageForwardResult
from synapse_channel.core.persistence import EventStore
from test_message_forward_retry_isolation import _enqueue


async def test_direct_serving_reopens_and_finalizes_the_same_checkpoint(tmp_path: Path) -> None:
    """Real clients still register/chat across two lifetimes on one caller store."""
    with EventStore(tmp_path / "hub.db") as store:
        hub = SynapseHub(hub_id="lifetime", journal=store, checkpoint_interval=0.01)
        checkpoint_path = hub._checkpoint_path
        assert checkpoint_path is not None
        for index in range(2):
            async with running_hub(hub) as (_, uri):
                async with connect(uri) as websocket:
                    await read_until_type(websocket, "welcome")
                    await websocket.send(json.dumps({"type": "heartbeat", "sender": "P/author"}))
                    await read_until_type(websocket, "presence_update")
                    await websocket.send(
                        json.dumps({"type": "chat", "sender": "P/author", "payload": str(index)})
                    )
                    chat = await read_until_type(websocket, "chat")
                    assert chat["payload"] == str(index)
                with closing(MerkleCheckpointStore(checkpoint_path)) as checkpoints:

                    def anchored() -> bool:
                        """Require the periodic anchor to include the actual latest chat."""
                        current = checkpoints.latest()
                        assert current is not None
                        return current.seq == store.max_seq()

                    await until(anchored)
            assert hub._live_checkpoint is None
            with closing(MerkleCheckpointStore(checkpoint_path)) as checkpoints:
                latest = checkpoints.latest()
                assert latest is not None and latest.seq == store.max_seq()
            store.delivery.verify_replay()


async def test_failed_direct_bind_retains_caller_checkpoint_until_close(tmp_path: Path) -> None:
    """A genuinely occupied port preserves startup failure and caller ownership."""
    with EventStore(tmp_path / "bind.db") as store:
        hub = SynapseHub(hub_id="bind-failure", journal=store)
        checkpoint = hub._live_checkpoint
        tasks_before = asyncio.all_tasks()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held.listen()
            with pytest.raises(OSError):
                await hub.serve("127.0.0.1", held.getsockname()[1])
        assert hub._live_checkpoint is checkpoint
        assert asyncio.all_tasks() <= tasks_before
        assert store.max_seq() == 0
        hub.close()
        assert hub._live_checkpoint is None


async def test_direct_stop_joins_actual_peer_io_before_final_store_release(tmp_path: Path) -> None:
    """Cancellation drains a real blocked peer read while the journal remains open."""
    started, drained = asyncio.Event(), asyncio.Event()
    received, release = asyncio.Event(), asyncio.Event()

    async def peer_handler(websocket: ServerConnection) -> None:
        """Hold an actual received request until the enclosing peer is released."""
        await websocket.recv()
        received.set()
        await release.wait()

    with EventStore(tmp_path / "drain.db") as store:

        async def forwarding(
            request: MessageForwardRequest, *, peer: MessageForwardPeer, local_id: str
        ) -> MessageForwardResult:
            """Delegate to real peer I/O and observe journal access during unwind."""
            started.set()
            try:
                return await forward_message(request, peer=peer, local_id=local_id)
            finally:
                assert store.max_seq() >= 0
                drained.set()

        async with serve(peer_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            hub = SynapseHub(
                hub_id="drain",
                journal=store,
                message_peers={"peer": MessageForwardPeer(f"ws://127.0.0.1:{port}")},
            )
            hub.message_forwarder = forwarding
            _enqueue(hub, "b" * 32, "peer")
            try:
                async with running_hub(hub):
                    await asyncio.wait_for(started.wait(), 5)
                    await asyncio.wait_for(received.wait(), 5)
            finally:
                release.set()
            assert drained.is_set()
            assert hub._live_checkpoint is None
            entry = store.message_forward.outbox_entry("b" * 32)
            assert entry is not None and entry.state == "pending"
