# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-host multi-hub event-log wire codec tests

from __future__ import annotations

import pytest

from synapse_channel.core.multihub_wire import (
    AFTER_SEQ_FIELD,
    EVENTS_FIELD,
    KIND_FIELD,
    LIMIT_FIELD,
    NEXT_CURSOR_FIELD,
    PAYLOAD_FIELD,
    SEQ_FIELD,
    TS_FIELD,
    LogRequest,
    LogSnapshot,
    MultiHubWireError,
    decode_log_request,
    decode_log_snapshot,
    decode_stored_event,
    encode_log_request,
    encode_log_snapshot,
    encode_stored_event,
)
from synapse_channel.core.persistence import StoredEvent


def _event(seq: int, *, ts: float = 1.5, kind: str = "chat") -> StoredEvent:
    """Return a stored event with a small chat payload for round-trip tests."""
    return StoredEvent(seq=seq, ts=ts, kind=kind, payload={"text": f"hello-{seq}"})


# --- stored event ------------------------------------------------------------------------


def test_encode_stored_event_emits_canonical_fields() -> None:
    body = encode_stored_event(_event(7, ts=12.0, kind="finding"))
    assert body == {
        SEQ_FIELD: 7,
        TS_FIELD: 12.0,
        KIND_FIELD: "finding",
        PAYLOAD_FIELD: {"text": "hello-7"},
    }


def test_stored_event_round_trips() -> None:
    event = _event(3)
    assert decode_stored_event(encode_stored_event(event)) == event


def test_encode_stored_event_coerces_integer_timestamp_to_float() -> None:
    body = encode_stored_event(StoredEvent(seq=1, ts=2, kind="chat", payload={}))
    assert body[TS_FIELD] == 2.0
    assert isinstance(body[TS_FIELD], float)


def test_encode_stored_event_rejects_negative_seq() -> None:
    with pytest.raises(MultiHubWireError, match="seq must not be negative"):
        encode_stored_event(StoredEvent(seq=-1, ts=1.0, kind="chat", payload={}))


def test_decode_stored_event_rejects_non_mapping() -> None:
    with pytest.raises(MultiHubWireError, match="event body must be a JSON object"):
        decode_stored_event(["not", "a", "mapping"])


def test_decode_stored_event_accepts_integer_timestamp() -> None:
    body = {SEQ_FIELD: 1, TS_FIELD: 4, KIND_FIELD: "chat", PAYLOAD_FIELD: {}}
    assert decode_stored_event(body).ts == 4.0


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({SEQ_FIELD: "x"}, "seq must be an integer"),
        ({SEQ_FIELD: True}, "seq must be an integer"),
        ({SEQ_FIELD: -2}, "seq must not be negative"),
        ({TS_FIELD: "soon"}, "ts must be a number"),
        ({TS_FIELD: True}, "ts must be a number"),
        ({KIND_FIELD: 5}, "kind must be a string"),
        ({PAYLOAD_FIELD: "flat"}, "payload body must be a JSON object"),
    ],
)
def test_decode_stored_event_rejects_bad_fields(mutation: dict[str, object], match: str) -> None:
    body: dict[str, object] = {SEQ_FIELD: 1, TS_FIELD: 1.0, KIND_FIELD: "chat", PAYLOAD_FIELD: {}}
    body.update(mutation)
    with pytest.raises(MultiHubWireError, match=match):
        decode_stored_event(body)


def test_decode_stored_event_copies_payload() -> None:
    payload = {"text": "shared"}
    decoded = decode_stored_event(
        {SEQ_FIELD: 1, TS_FIELD: 1.0, KIND_FIELD: "chat", PAYLOAD_FIELD: payload}
    )
    payload["text"] = "mutated"
    assert decoded.payload == {"text": "shared"}


# --- log request -------------------------------------------------------------------------


def test_encode_log_request_includes_limit() -> None:
    assert encode_log_request(LogRequest(after_seq=5, limit=10)) == {
        AFTER_SEQ_FIELD: 5,
        LIMIT_FIELD: 10,
    }


def test_encode_log_request_uncapped_emits_null_limit() -> None:
    assert encode_log_request(LogRequest(after_seq=0)) == {AFTER_SEQ_FIELD: 0, LIMIT_FIELD: None}


def test_log_request_round_trips_with_limit() -> None:
    request = LogRequest(after_seq=12, limit=4)
    assert decode_log_request(encode_log_request(request)) == request


def test_log_request_round_trips_without_limit() -> None:
    request = LogRequest(after_seq=12)
    assert decode_log_request(encode_log_request(request)) == request


def test_encode_log_request_rejects_negative_after_seq() -> None:
    with pytest.raises(MultiHubWireError, match="after_seq must not be negative"):
        encode_log_request(LogRequest(after_seq=-1))


def test_encode_log_request_rejects_negative_limit() -> None:
    with pytest.raises(MultiHubWireError, match="limit must not be negative"):
        encode_log_request(LogRequest(after_seq=0, limit=-3))


def test_decode_log_request_rejects_non_mapping() -> None:
    with pytest.raises(MultiHubWireError, match="request body must be a JSON object"):
        decode_log_request(7)


def test_decode_log_request_rejects_missing_after_seq() -> None:
    with pytest.raises(MultiHubWireError, match="after_seq must be an integer"):
        decode_log_request({LIMIT_FIELD: 5})


def test_decode_log_request_rejects_non_integer_limit() -> None:
    with pytest.raises(MultiHubWireError, match="limit must be an integer"):
        decode_log_request({AFTER_SEQ_FIELD: 0, LIMIT_FIELD: "lots"})


def test_decode_log_request_absent_limit_is_none() -> None:
    assert decode_log_request({AFTER_SEQ_FIELD: 9}).limit is None


# --- log snapshot ------------------------------------------------------------------------


def test_encode_log_snapshot_emits_events_and_cursor() -> None:
    snapshot = LogSnapshot(events=(_event(1), _event(2)), next_cursor=2)
    body = encode_log_snapshot(snapshot)
    assert body[NEXT_CURSOR_FIELD] == 2
    assert [event[SEQ_FIELD] for event in body[EVENTS_FIELD]] == [1, 2]


def test_log_snapshot_round_trips() -> None:
    snapshot = LogSnapshot(events=(_event(4), _event(5), _event(6)), next_cursor=6)
    assert decode_log_snapshot(encode_log_snapshot(snapshot)) == snapshot


def test_empty_log_snapshot_round_trips() -> None:
    snapshot = LogSnapshot(events=(), next_cursor=3)
    assert decode_log_snapshot(encode_log_snapshot(snapshot)) == snapshot


def test_encode_log_snapshot_rejects_negative_cursor() -> None:
    with pytest.raises(MultiHubWireError, match="next_cursor must not be negative"):
        encode_log_snapshot(LogSnapshot(events=(), next_cursor=-1))


def test_decode_log_snapshot_rejects_non_mapping() -> None:
    with pytest.raises(MultiHubWireError, match="snapshot body must be a JSON object"):
        decode_log_snapshot("nope")


@pytest.mark.parametrize("events", ["a string", b"bytes", 7, {"not": "a list"}])
def test_decode_log_snapshot_rejects_non_list_events(events: object) -> None:
    with pytest.raises(MultiHubWireError, match="snapshot 'events' must be a list of events"):
        decode_log_snapshot({EVENTS_FIELD: events, NEXT_CURSOR_FIELD: 0})


def test_decode_log_snapshot_propagates_bad_event() -> None:
    with pytest.raises(MultiHubWireError, match="seq must be an integer"):
        decode_log_snapshot({EVENTS_FIELD: [{SEQ_FIELD: "x"}], NEXT_CURSOR_FIELD: 0})


def test_decode_log_snapshot_rejects_missing_cursor() -> None:
    with pytest.raises(MultiHubWireError, match="next_cursor must be an integer"):
        decode_log_snapshot({EVENTS_FIELD: []})
