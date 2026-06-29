# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound streaming-response wiring regressions

from __future__ import annotations

from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
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
            stream_id = await sender.agent.stream_reply(["hel", "lo "], target="RX")
            await receiver.recorder.wait_for(
                lambda message: (
                    message.get("kind") == "stream" and message.get("frame_type") == "done"
                )
            )
        finally:
            await close_agents(sender, receiver)

    consumer = _reassemble(receiver.recorder.messages, stream_id)
    assert consumer.text == "hello "
    assert consumer.closed is True
    assert consumer.aborted is False


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
