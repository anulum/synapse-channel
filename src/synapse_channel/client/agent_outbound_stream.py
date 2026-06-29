# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound bounded streaming-response helpers
"""Outbound streaming-response helpers for the reusable client.

A worker or long-running task sends an incremental reply as a bounded stream:
one ``open`` frame, ordered ``chunk`` frames, and a terminal ``done`` (or
``abort``) frame, each carried as a chat envelope tagged with the stream id. The
producer enforces the :class:`~synapse_channel.core.streaming.StreamBounds`
ceiling, so a runaway generation is refused at the source. A receiver reassembles
the frames with :class:`~synapse_channel.core.streaming.StreamConsumer`.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.streaming import (
    StreamBounds,
    StreamFrame,
    StreamProducer,
    encode_stream_frame,
)

__all__ = ["AgentStreamMixin"]


class AgentStreamMixin:
    """Send a bounded streaming response as ordered, chat-carried frames."""

    async def stream_reply(
        self: _OutboundAgent,
        chunks: Iterable[str],
        *,
        target: str = "all",
        stream_id: str = "",
        bounds: StreamBounds | None = None,
        channel: str = "",
    ) -> str:
        """Emit a bounded ``open``/``chunk``…/``done`` stream over the chat path.

        Parameters
        ----------
        chunks : Iterable[str]
            Ordered body chunks to stream.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        stream_id : str, optional
            Stream id to use; a random url-safe id is generated when empty.
        bounds : StreamBounds or None, optional
            Ceiling enforced as frames are produced; the library default applies
            when omitted.
        channel : str, optional
            Private channel to scope the stream to its members.

        Returns
        -------
        str
            The stream id every frame was tagged with.

        Raises
        ------
        synapse_channel.core.streaming.StreamError
            If a chunk would exceed the declared bounds; frames emitted before the
            overflow have already been sent.
        """
        sid = stream_id.strip() or secrets.token_urlsafe(12)
        producer = StreamProducer(sid, bounds=bounds)
        await _emit_frame(self, producer.open(), target=target, channel=channel)
        for text in chunks:
            await _emit_frame(self, producer.chunk(text), target=target, channel=channel)
        await _emit_frame(self, producer.done(), target=target, channel=channel)
        return sid


async def _emit_frame(
    agent: _OutboundAgent, frame: StreamFrame, *, target: str, channel: str
) -> None:
    """Send one stream frame as a chat envelope carrying the frame fields."""
    extra: dict[str, object] = dict(encode_stream_frame(frame))
    if channel:
        extra["channel"] = channel
    await agent.send_message(MessageType.CHAT, target=target, payload=frame.text, **extra)
