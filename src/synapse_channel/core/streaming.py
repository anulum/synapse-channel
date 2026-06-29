# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded streaming response frames for incremental replies
"""A bounded streaming-response path for worker replies and long-running progress.

A single chat reply is fine for a short answer, but a long generation or a
long-running task wants to deliver its output incrementally. This module defines
that as an explicit, *bounded* sequence of frames carried over the existing
WebSocket message path — an ``open`` frame, ordered ``chunk`` frames, and a
terminal ``done`` or ``abort`` frame, all tagged with one stream id.

The bounds are the point. A stream declares a :class:`StreamBounds` ceiling
(chunk count, per-chunk bytes, total bytes, and a time-to-live), and the producer
refuses to exceed it rather than letting a runaway generation flood the bus. The
consumer enforces the same ceiling and ordered delivery on the receiving side, so
a malformed or oversized stream is rejected, not reassembled.

Retention is deliberately shallow: a stream is *transient* coordination, not
durable task state. The frames ride the chat path and are subject to the same
relay mirroring as chat, but they are bounded by the producer and are not replay
state — the durable record of what a task produced is its release receipt and
final reply, not the intermediate chunks. A future tranche may add a
non-journalled stream message type for fully ephemeral delivery.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

STREAM_FRAME_KIND = "stream"
"""Marker identifying a message payload as a stream frame."""

OPEN = "open"
"""Frame type opening a stream."""

CHUNK = "chunk"
"""Frame type carrying one ordered body chunk."""

DONE = "done"
"""Terminal frame type marking a complete stream."""

ABORT = "abort"
"""Terminal frame type marking a stream the producer gave up on."""

_FRAME_TYPES = frozenset({OPEN, CHUNK, DONE, ABORT})


class StreamError(ValueError):
    """Raised when a stream operation would violate its declared bounds or order."""


@dataclass(frozen=True)
class StreamBounds:
    """Explicit ceiling a stream may not exceed.

    Parameters
    ----------
    max_chunks : int
        Maximum number of ``chunk`` frames before the producer must finish.
    max_chunk_bytes : int
        Maximum UTF-8 byte length of a single chunk body.
    max_total_bytes : int
        Maximum cumulative UTF-8 byte length across all chunk bodies.
    ttl_seconds : float
        Wall-clock budget for the whole stream, from open to terminal frame.
    """

    max_chunks: int = 256
    max_chunk_bytes: int = 16384
    max_total_bytes: int = 1048576
    ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        """Reject non-positive bounds, which would make a stream unusable."""
        if min(self.max_chunks, self.max_chunk_bytes, self.max_total_bytes) <= 0:
            msg = "stream bounds must be positive"
            raise StreamError(msg)
        if self.ttl_seconds <= 0:
            msg = "stream ttl must be positive"
            raise StreamError(msg)


@dataclass(frozen=True)
class StreamFrame:
    """One frame of a stream.

    Attributes
    ----------
    stream_id : str
        Stable id shared by every frame of one stream.
    seq : int
        Zero-based frame sequence number; the ``open`` frame is ``0``.
    frame_type : str
        One of ``open``, ``chunk``, ``done``, or ``abort``.
    text : str
        Chunk body for a ``chunk`` frame, an abort reason for ``abort``, else "".
    """

    stream_id: str
    seq: int
    frame_type: str
    text: str = ""


def encode_stream_frame(frame: StreamFrame) -> dict[str, Any]:
    """Return the canonical message payload for a stream frame.

    Parameters
    ----------
    frame : StreamFrame
        The frame to serialise.

    Returns
    -------
    dict[str, Any]
        Payload carrying the stream marker and frame fields.
    """
    return {
        "kind": STREAM_FRAME_KIND,
        "stream_id": frame.stream_id,
        "seq": frame.seq,
        "frame_type": frame.frame_type,
        "text": frame.text,
    }


def parse_stream_frame(payload: dict[str, Any]) -> StreamFrame | None:
    """Parse a message payload into a stream frame.

    Parameters
    ----------
    payload : dict[str, Any]
        A received message payload.

    Returns
    -------
    StreamFrame or None
        The parsed frame, or ``None`` when the payload is not a well-formed stream
        frame (wrong marker, unknown type, missing id, or non-integer sequence).
    """
    if str(payload.get("kind", "")) != STREAM_FRAME_KIND:
        return None
    stream_id = str(payload.get("stream_id", "")).strip()
    frame_type = str(payload.get("frame_type", ""))
    if not stream_id or frame_type not in _FRAME_TYPES:
        return None
    try:
        seq = int(payload.get("seq"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if seq < 0:
        return None
    return StreamFrame(
        stream_id=stream_id,
        seq=seq,
        frame_type=frame_type,
        text=str(payload.get("text", "")),
    )


class StreamProducer:
    """Build a bounded, ordered sequence of frames for one stream.

    The producer enforces :class:`StreamBounds` as frames are requested: a chunk
    past the count ceiling, an oversized chunk, or a cumulative overflow raises
    :class:`StreamError` instead of emitting, so a runaway generation is stopped
    at the source rather than on the bus.
    """

    def __init__(self, stream_id: str, *, bounds: StreamBounds | None = None) -> None:
        cleaned = stream_id.strip()
        if not cleaned:
            msg = "stream id must be non-empty"
            raise StreamError(msg)
        self.stream_id = cleaned
        self.bounds = bounds or StreamBounds()
        self._seq = 0
        self._chunks = 0
        self._total_bytes = 0
        self._opened = False
        self._closed = False

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def open(self) -> StreamFrame:
        """Emit the opening frame; must be called once before any chunk."""
        if self._opened:
            msg = "stream already opened"
            raise StreamError(msg)
        self._opened = True
        return StreamFrame(self.stream_id, self._next_seq(), OPEN)

    def chunk(self, text: str) -> StreamFrame:
        """Emit one body chunk, enforcing the count and byte ceilings."""
        if not self._opened or self._closed:
            msg = "stream is not open"
            raise StreamError(msg)
        size = len(text.encode("utf-8"))
        if size > self.bounds.max_chunk_bytes:
            msg = f"chunk of {size} bytes exceeds max_chunk_bytes {self.bounds.max_chunk_bytes}"
            raise StreamError(msg)
        if self._chunks >= self.bounds.max_chunks:
            msg = f"stream exceeded max_chunks {self.bounds.max_chunks}"
            raise StreamError(msg)
        if self._total_bytes + size > self.bounds.max_total_bytes:
            msg = f"stream exceeded max_total_bytes {self.bounds.max_total_bytes}"
            raise StreamError(msg)
        self._chunks += 1
        self._total_bytes += size
        return StreamFrame(self.stream_id, self._next_seq(), CHUNK, text)

    def done(self) -> StreamFrame:
        """Emit the terminal ``done`` frame."""
        return self._close(DONE, "")

    def abort(self, reason: str = "") -> StreamFrame:
        """Emit the terminal ``abort`` frame with an optional reason."""
        return self._close(ABORT, " ".join(reason.split()))

    def _close(self, frame_type: str, text: str) -> StreamFrame:
        if not self._opened:
            msg = "stream is not open"
            raise StreamError(msg)
        if self._closed:
            msg = "stream already closed"
            raise StreamError(msg)
        self._closed = True
        return StreamFrame(self.stream_id, self._next_seq(), frame_type, text)

    def stream(self, chunks: Iterator[str] | list[str]) -> list[StreamFrame]:
        """Produce a full open/chunk.../done frame list for an iterable of chunks."""
        frames = [self.open()]
        frames.extend(self.chunk(text) for text in chunks)
        frames.append(self.done())
        return frames


class StreamConsumer:
    """Reassemble a bounded, ordered stream on the receiving side.

    The consumer validates the matching stream id, strict in-order sequence, the
    open-before-chunk discipline, and the same :class:`StreamBounds` ceiling. A
    frame that breaks any of these is rejected with :class:`StreamError`, so a
    consumer never reassembles a malformed or oversized stream.
    """

    def __init__(self, stream_id: str, *, bounds: StreamBounds | None = None) -> None:
        self.stream_id = stream_id.strip()
        self.bounds = bounds or StreamBounds()
        self._expected_seq = 0
        self._opened = False
        self._closed = False
        self._chunks = 0
        self._total_bytes = 0
        self._parts: list[str] = []
        self.aborted = False
        self.abort_reason = ""

    @property
    def text(self) -> str:
        """Return the body reassembled from chunks accepted so far."""
        return "".join(self._parts)

    @property
    def closed(self) -> bool:
        """Return whether a terminal frame (done or abort) was accepted."""
        return self._closed

    def accept(self, frame: StreamFrame) -> str:
        """Apply one frame in order and return its type.

        Raises
        ------
        StreamError
            If the frame is for another stream, out of order, arrives after the
            stream closed, opens twice, chunks before open, or breaks a bound.
        """
        if frame.stream_id != self.stream_id:
            msg = f"frame for stream {frame.stream_id!r} on consumer {self.stream_id!r}"
            raise StreamError(msg)
        if self._closed:
            msg = "stream already closed"
            raise StreamError(msg)
        if frame.seq != self._expected_seq:
            msg = f"out-of-order frame seq {frame.seq}, expected {self._expected_seq}"
            raise StreamError(msg)
        # Only advance the expected sequence once the frame is accepted, so a
        # rejected frame (chunk before open, bound overflow) does not desync the
        # consumer's idea of the next sequence number.
        if frame.frame_type == OPEN:
            result = self._accept_open()
        elif frame.frame_type == CHUNK:
            result = self._accept_chunk(frame.text)
        else:
            result = self._accept_terminal(frame)
        self._expected_seq += 1
        return result

    def _accept_open(self) -> str:
        if self._opened:
            msg = "stream opened twice"
            raise StreamError(msg)
        self._opened = True
        return OPEN

    def _accept_chunk(self, text: str) -> str:
        if not self._opened:
            msg = "chunk before open"
            raise StreamError(msg)
        size = len(text.encode("utf-8"))
        if size > self.bounds.max_chunk_bytes or self._chunks >= self.bounds.max_chunks:
            msg = "stream chunk exceeds declared bounds"
            raise StreamError(msg)
        if self._total_bytes + size > self.bounds.max_total_bytes:
            msg = "stream exceeded max_total_bytes"
            raise StreamError(msg)
        self._chunks += 1
        self._total_bytes += size
        self._parts.append(text)
        return CHUNK

    def _accept_terminal(self, frame: StreamFrame) -> str:
        if not self._opened:
            msg = "terminal frame before open"
            raise StreamError(msg)
        self._closed = True
        if frame.frame_type == ABORT:
            self.aborted = True
            self.abort_reason = frame.text
        return frame.frame_type
