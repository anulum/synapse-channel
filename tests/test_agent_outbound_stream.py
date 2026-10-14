# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound streaming-response wiring regressions

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.streaming import (
    StreamBounds,
    StreamConsumer,
    StreamError,
    parse_stream_frame,
)


def _reassemble(messages: list[dict[str, Any]], stream_id: str) -> StreamConsumer:
    consumer = StreamConsumer(stream_id)
    frames = sorted(
        (
            frame
            for message in messages
            if (frame := parse_stream_frame(message)) is not None and frame.stream_id == stream_id
        ),
        key=lambda frame: frame.seq,
    )
    for frame in frames:
        consumer.accept(frame)
    return consumer


async def test_stream_reply_delivers_a_reassemblable_bounded_stream() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        receiver = await connect_agent("RX", uri)
        sender = await connect_agent("TX", uri)
        try:
            stream_id = await sender.agent.stream_reply(["foo", "bar "], target="RX")
            await receiver.recorder.wait_for(
                lambda message: (
                    message.get("kind") == "stream" and message.get("frame_type") == "done"
                )
            )
        finally:
            await close_agents(sender, receiver)

    consumer = _reassemble(receiver.recorder.messages, stream_id)
    assert consumer.text == "foobar "
    assert consumer.closed is True
    assert consumer.aborted is False


async def test_stream_declared_lifetime_does_not_expire_or_prevent_chat_retention(
    tmp_path: Path,
) -> None:
    """A real stream can complete after its declared TTL and retains chunks as chat rows."""
    bounds = StreamBounds(ttl_seconds=0.001)

    def chunks() -> Iterator[str]:
        """Yield two chunks separated by more than the declared lifetime."""
        yield "before "
        time.sleep(0.02)
        yield "after"

    store = EventStore(tmp_path / "events.db")
    try:
        async with running_hub(SynapseHub(journal=store, anti_rollback_checkpoint=False)) as (
            _,
            uri,
        ):
            receiver = await connect_agent("RX", uri)
            sender = await connect_agent("TX", uri)
            try:
                started = time.monotonic()
                stream_id = await sender.agent.stream_reply(chunks(), target="RX", bounds=bounds)
                await receiver.recorder.wait_for(
                    lambda message: (
                        message.get("stream_id") == stream_id
                        and message.get("frame_type") == "done"
                    )
                )
                elapsed = time.monotonic() - started
            finally:
                await close_agents(sender, receiver)
        consumer = StreamConsumer(stream_id, bounds=bounds)
        for message in receiver.recorder.messages:
            frame = parse_stream_frame(message)
            if frame is not None and frame.stream_id == stream_id:
                consumer.accept(frame)
        assert elapsed > bounds.ttl_seconds and consumer.closed
        assert consumer.text == "before after"
        frames = [
            frame
            for row in store.iter_events(kinds=[EventKind.CHAT])
            if (frame := parse_stream_frame(row.payload)) is not None
            and frame.stream_id == stream_id
        ]
        assert [frame.frame_type for frame in frames] == ["open", "chunk", "chunk", "done"]
        assert [frame.text for frame in frames if frame.frame_type == "chunk"] == [
            "before ",
            "after",
        ]
        assert not list(store.iter_events(kinds=[EventKind.RELEASE]))
    finally:
        store.close()


async def test_stream_reply_refuses_to_exceed_its_bounds() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        sender = await connect_agent("TX", uri)
        try:
            with pytest.raises(StreamError, match="max_chunks"):
                await sender.agent.stream_reply(
                    ["a", "b", "c"], target="all", bounds=StreamBounds(max_chunks=2)
                )
        finally:
            await close_agents(sender)


async def test_journalled_stream_resumes_from_a_midstream_cursor_over_the_real_hub(
    tmp_path: Path,
) -> None:
    """A reconnected recipient keeps frame order across paged durable inbox replay."""
    store = EventStore(tmp_path / "resume.db")
    try:
        hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
        async with running_hub(hub) as (_, uri):
            receiver = await connect_agent("RX", uri)
            sender = await connect_agent("TX", uri)
            try:
                await sender.agent.send_message("chat", target="RX", payload="before stream")
                await receiver.recorder.wait_for(lambda m: m.get("payload") == "before stream")
                stream_id = await sender.agent.stream_reply(["A", "B", "C"], target="RX")
                await receiver.recorder.wait_for(
                    lambda m: m.get("stream_id") == stream_id and m.get("frame_type") == "done"
                )
                live = [m for m in receiver.recorder.messages if m.get("stream_id") == stream_id]
                consumer = StreamConsumer(stream_id)
                for message in live[:2]:
                    frame = parse_stream_frame(message)
                    assert frame is not None
                    consumer.accept(frame)
                cursor = int(live[1]["seq"])
                assert not consumer.closed and consumer.text == "A"
                assert cursor != int(live[1]["stream_seq"])
                await close_agents(receiver)
                resumed = await connect_agent("RX", uri)
                try:
                    for page_index in range(3):
                        request_id = f"stream-page-{page_index}"
                        await resumed.agent.send_message(
                            "history_request",
                            target="System",
                            request_id=request_id,
                            inbox_query={
                                "version": 1,
                                "identity": "RX",
                                "hub_id": hub.hub_id,
                                "since_seq": cursor,
                                "limit": 1,
                            },
                        )

                        def expected_page(
                            message: dict[str, Any], *, correlation: str = request_id
                        ) -> bool:
                            """Match this page's response without sharing the next loop's id."""
                            return message.get("request_id") == correlation

                        response = await resumed.recorder.wait_for(expected_page)
                        page = response["inbox_page"]
                        assert page["available"] is True and len(page["messages"]) == 1
                        message = page["messages"][0]
                        assert message["stream_seq"] == page_index + 2
                        assert int(message["seq"]) > cursor
                        frame = parse_stream_frame(message)
                        assert frame is not None
                        consumer.accept(frame)
                        cursor = int(page["cursor"])
                    assert consumer.closed and consumer.text == "ABC"
                finally:
                    await close_agents(resumed)
            finally:
                await close_agents(sender, receiver)
    finally:
        store.close()


async def test_emit_frame_scopes_to_a_channel() -> None:
    from synapse_channel.client.agent_outbound_stream import _emit_frame
    from synapse_channel.core.streaming import OPEN, StreamFrame

    calls: list[dict[str, Any]] = []

    class _Recorder:
        async def send_message(
            self, msg_type: str, *, target: str = "all", payload: str = "", **extra: Any
        ) -> None:
            calls.append({"type": msg_type, "target": target, "payload": payload, **extra})

    await _emit_frame(
        _Recorder(),  # type: ignore[arg-type]
        StreamFrame("S1", 0, OPEN),
        target="RX",
        channel="ops",
    )
    assert calls[0]["channel"] == "ops"
    assert calls[0]["target"] == "RX"
    assert calls[0]["kind"] == "stream"
