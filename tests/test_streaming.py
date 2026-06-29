# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded streaming response regressions

from __future__ import annotations

import pytest

from synapse_channel.core.streaming import (
    ABORT,
    CHUNK,
    DONE,
    OPEN,
    StreamBounds,
    StreamConsumer,
    StreamError,
    StreamFrame,
    StreamProducer,
    encode_stream_frame,
    parse_stream_frame,
)

# ---------- StreamBounds ----------


def test_stream_bounds_defaults_are_positive() -> None:
    bounds = StreamBounds()
    assert bounds.max_chunks > 0 and bounds.ttl_seconds > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_chunks": 0},
        {"max_chunk_bytes": 0},
        {"max_total_bytes": -1},
        {"ttl_seconds": 0.0},
    ],
)
def test_stream_bounds_reject_non_positive(kwargs: dict[str, float]) -> None:
    with pytest.raises(StreamError):
        StreamBounds(**kwargs)  # type: ignore[arg-type]


# ---------- encode / parse ----------


def test_encode_parse_round_trip() -> None:
    frame = StreamFrame("S1", 2, CHUNK, "hello")
    assert parse_stream_frame(encode_stream_frame(frame)) == frame


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "note", "stream_id": "S1", "seq": 0, "frame_type": OPEN},
        {"kind": "stream", "stream_id": "", "seq": 0, "frame_type": OPEN},
        {"kind": "stream", "stream_id": "S1", "seq": 0, "frame_type": "bogus"},
        {"kind": "stream", "stream_id": "S1", "seq": "x", "frame_type": OPEN},
        {"kind": "stream", "stream_id": "S1", "seq": -1, "frame_type": OPEN},
    ],
)
def test_parse_rejects_malformed(payload: dict[str, object]) -> None:
    assert parse_stream_frame(payload) is None


# ---------- StreamProducer ----------


def test_producer_requires_non_empty_id() -> None:
    with pytest.raises(StreamError):
        StreamProducer("   ")


def test_producer_open_chunk_done_sequence_is_ordered() -> None:
    producer = StreamProducer("S1")
    frames = [producer.open(), producer.chunk("a"), producer.chunk("b"), producer.done()]
    assert [f.seq for f in frames] == [0, 1, 2, 3]
    assert [f.frame_type for f in frames] == [OPEN, CHUNK, CHUNK, DONE]


def test_producer_rejects_double_open_and_chunk_before_open() -> None:
    producer = StreamProducer("S1")
    with pytest.raises(StreamError, match="not open"):
        producer.chunk("early")
    producer.open()
    with pytest.raises(StreamError, match="already opened"):
        producer.open()


def test_producer_enforces_chunk_byte_and_count_and_total_bounds() -> None:
    producer = StreamProducer(
        "S1", bounds=StreamBounds(max_chunks=2, max_chunk_bytes=4, max_total_bytes=6)
    )
    producer.open()
    with pytest.raises(StreamError, match="max_chunk_bytes"):
        producer.chunk("toolong")
    producer.chunk("ab")  # 2 bytes
    producer.chunk("abcd")  # 4 bytes -> total 6
    with pytest.raises(StreamError, match="max_chunks"):
        producer.chunk("x")


def test_producer_enforces_total_byte_bound_before_count() -> None:
    producer = StreamProducer(
        "S1", bounds=StreamBounds(max_chunks=10, max_chunk_bytes=8, max_total_bytes=4)
    )
    producer.open()
    producer.chunk("abc")  # 3 bytes
    with pytest.raises(StreamError, match="max_total_bytes"):
        producer.chunk("bb")  # would be 5 > 4


def test_producer_abort_collapses_reason_and_blocks_reuse() -> None:
    producer = StreamProducer("S1")
    producer.open()
    frame = producer.abort("gave   up\nnow")
    assert frame.frame_type == ABORT
    assert frame.text == "gave up now"
    with pytest.raises(StreamError, match="already closed"):
        producer.done()
    with pytest.raises(StreamError, match="not open"):
        producer.chunk("after")


def test_producer_close_before_open_raises() -> None:
    with pytest.raises(StreamError, match="not open"):
        StreamProducer("S1").done()


def test_producer_stream_helper_builds_full_sequence() -> None:
    frames = StreamProducer("S1").stream(["x", "y"])
    assert [f.frame_type for f in frames] == [OPEN, CHUNK, CHUNK, DONE]


# ---------- StreamConsumer ----------


def test_consumer_reassembles_a_full_stream() -> None:
    producer = StreamProducer("S1")
    consumer = StreamConsumer("S1")
    types = [consumer.accept(frame) for frame in producer.stream(["foo", "bar"])]
    assert types == [OPEN, CHUNK, CHUNK, DONE]
    assert consumer.text == "foobar"
    assert consumer.closed is True
    assert consumer.aborted is False


def test_consumer_records_abort_reason() -> None:
    producer = StreamProducer("S1")
    consumer = StreamConsumer("S1")
    consumer.accept(producer.open())
    consumer.accept(producer.chunk("partial"))
    assert consumer.accept(producer.abort("timeout")) == ABORT
    assert consumer.aborted is True and consumer.abort_reason == "timeout"
    assert consumer.text == "partial"


def test_consumer_rejects_foreign_stream_id() -> None:
    consumer = StreamConsumer("S1")
    with pytest.raises(StreamError, match="consumer"):
        consumer.accept(StreamFrame("OTHER", 0, OPEN))


def test_consumer_rejects_out_of_order_and_post_close() -> None:
    producer = StreamProducer("S1")
    consumer = StreamConsumer("S1")
    consumer.accept(producer.open())
    with pytest.raises(StreamError, match="out-of-order"):
        consumer.accept(StreamFrame("S1", 5, CHUNK, "skip"))
    consumer.accept(producer.chunk("a"))
    consumer.accept(producer.done())
    with pytest.raises(StreamError, match="already closed"):
        consumer.accept(StreamFrame("S1", 3, DONE))


def test_consumer_rejects_double_open_and_chunk_before_open() -> None:
    consumer = StreamConsumer("S1")
    with pytest.raises(StreamError, match="before open"):
        consumer.accept(StreamFrame("S1", 0, CHUNK, "x"))
    consumer.accept(StreamFrame("S1", 0, OPEN))
    with pytest.raises(StreamError, match="opened twice"):
        consumer.accept(StreamFrame("S1", 1, OPEN))


def test_consumer_enforces_bounds() -> None:
    consumer = StreamConsumer(
        "S1", bounds=StreamBounds(max_chunks=1, max_chunk_bytes=4, max_total_bytes=4)
    )
    consumer.accept(StreamFrame("S1", 0, OPEN))
    with pytest.raises(StreamError, match="declared bounds"):
        consumer.accept(StreamFrame("S1", 1, CHUNK, "toolong"))


def test_consumer_enforces_total_bytes_bound() -> None:
    consumer = StreamConsumer(
        "S1", bounds=StreamBounds(max_chunks=10, max_chunk_bytes=8, max_total_bytes=4)
    )
    consumer.accept(StreamFrame("S1", 0, OPEN))
    consumer.accept(StreamFrame("S1", 1, CHUNK, "abc"))
    with pytest.raises(StreamError, match="max_total_bytes"):
        consumer.accept(StreamFrame("S1", 2, CHUNK, "bb"))


def test_consumer_rejects_terminal_before_open() -> None:
    consumer = StreamConsumer("S1")
    with pytest.raises(StreamError, match="terminal frame before open"):
        consumer.accept(StreamFrame("S1", 0, DONE))
