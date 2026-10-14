# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stream replay through actual relay files and two hubs
"""Exercise an explicit application bridge, not automatic federation delivery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.streaming import StreamConsumer, parse_stream_frame
from synapse_channel.relay import decode_lite, read_jsonl_since

_CHUNKS = ["Alpha ", "β🙂\n", "omega"]


@asynccontextmanager
async def _journalled_hub(root: Path, hub_id: str) -> AsyncIterator[tuple[EventStore, Path, str]]:
    """Serve one real loopback hub with its own SQLite journal and lite relay file."""
    root.mkdir()
    relay = root / "relay.ndjson"
    with EventStore(root / "events.db") as store:
        hub = SynapseHub(
            hub_id=hub_id, journal=store, relay_log=relay, anti_rollback_checkpoint=False
        )
        async with running_hub(hub) as (_, uri):
            yield store, relay, uri


@asynccontextmanager
async def _agents(uri: str) -> AsyncIterator[tuple[AgentHandle, AgentHandle]]:
    """Connect a sender and receiver and close each successfully created agent."""
    async with AsyncExitStack() as stack:
        receiver = await connect_agent("RX", uri)
        stack.push_async_callback(receiver.close)
        sender = await connect_agent("TX", uri)
        stack.push_async_callback(sender.close)
        yield sender, receiver


async def _relay_frames(path: Path, stream_id: str) -> list[dict[str, Any]]:
    """Wait for the real mirror to finish and decode its complete ordered stream."""

    async def read() -> list[dict[str, Any]]:
        """Read mirror output until every expected frame has actually been appended."""
        while True:
            encoded, _offset = read_jsonl_since(path, 0)
            messages = [decode_lite(row) for row in encoded]
            frames = [message for message in messages if message.get("stream_id") == stream_id]
            if len(frames) == len(_CHUNKS) + 2:
                return frames
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(read(), timeout=3)


async def _produce(uri: str) -> tuple[str, list[dict[str, Any]]]:
    """Use the public producer and wait for actual recipient delivery."""
    async with _agents(uri) as (sender, receiver):
        await sender.agent.send_message("chat", target="RX", payload="source prelude")
        await receiver.recorder.wait_for(lambda message: message.get("payload") == "source prelude")
        stream_id = await sender.agent.stream_reply(_CHUNKS, target="RX")
        await receiver.recorder.wait_for(
            lambda message: (
                message.get("stream_id") == stream_id and message.get("frame_type") == "done"
            )
        )
        return stream_id, [
            message
            for message in receiver.recorder.messages
            if message.get("stream_id") == stream_id
        ]


async def _forward(uri: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explicitly re-submit decoded frames using SDK chat; the second hub owns its cursor."""
    stream_id = messages[0]["stream_id"]
    async with _agents(uri) as (sender, receiver):
        for index in range(7):
            payload = f"destination prelude {index}"
            await sender.agent.send_message("chat", target="RX", payload=payload)
        for message in messages:
            fields = {
                key: message[key]
                for key in ("kind", "stream_id", "stream_seq", "seq", "frame_type", "text")
            }
            await sender.agent.send_message(
                "chat", target="RX", payload=message["payload"], **fields
            )
        await receiver.recorder.wait_for(
            lambda message: (
                message.get("stream_id") == stream_id and message.get("frame_type") == "done"
            )
        )
        return [
            message
            for message in receiver.recorder.messages
            if message.get("stream_id") == stream_id
        ]


def _assert_complete(messages: list[dict[str, Any]], stream_id: str) -> list[int]:
    """Assert order and exact Unicode body without sorting or inventing a cursor offset."""
    consumer = StreamConsumer(stream_id)
    assert [message["stream_seq"] for message in messages] == list(range(len(_CHUNKS) + 2))
    for message in messages:
        frame = parse_stream_frame(message)
        assert frame is not None
        consumer.accept(frame)
    assert consumer.closed and not consumer.aborted
    assert consumer.text == "".join(_CHUNKS)
    return [int(message["seq"]) for message in messages]


async def test_stream_survives_relay_decode_and_explicit_second_hub_forwarding(
    tmp_path: Path,
) -> None:
    """Both real journals and mirrors preserve frame order across different durable cursors."""
    async with (
        _journalled_hub(tmp_path / "source", "syn-source") as (source, source_relay, source_uri),
        _journalled_hub(tmp_path / "destination", "syn-destination") as (
            destination,
            destination_relay,
            destination_uri,
        ),
    ):
        stream_id, original = await _produce(source_uri)
        mirrored = await _relay_frames(source_relay, stream_id)
        assert not list(destination.iter_events(kinds=[EventKind.CHAT]))
        delivered = await _forward(destination_uri, mirrored)
        forwarded = await _relay_frames(destination_relay, stream_id)
        source_cursor = _assert_complete(original, stream_id)
        destination_cursor = _assert_complete(delivered, stream_id)
        assert source_cursor == _assert_complete(mirrored, stream_id)
        assert destination_cursor == _assert_complete(forwarded, stream_id)
        assert source_cursor != destination_cursor
        for store, expected_cursor in ((source, source_cursor), (destination, destination_cursor)):
            rows = [
                row
                for row in store.iter_events(kinds=[EventKind.CHAT])
                if row.payload.get("stream_id") == stream_id
            ]
            assert [row.seq for row in rows] == expected_cursor
            _assert_complete([row.payload for row in rows], stream_id)
            assert not list(store.iter_events(kinds=[EventKind.RELEASE]))
