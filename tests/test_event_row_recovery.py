# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for bounded corrupt event-row decoding

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core import event_row_recovery as recovery
from synapse_channel.core.event_row_recovery import (
    CORRUPT_EVENT_KIND,
    CorruptEventReason,
    CorruptEventRow,
    decode_event_row,
)


def test_valid_row_round_trips_without_a_marker() -> None:
    decoded = decode_event_row((7, 12.5, "claim", '{"task_id":"T1"}'))

    assert (decoded.seq, decoded.ts, decoded.kind) == (7, 12.5, "claim")
    assert decoded.payload == {"task_id": "T1"}
    assert decoded.corruption is None


@pytest.mark.parametrize("timestamp", [True, "12", float("inf"), float("nan")])
def test_invalid_timestamp_is_zeroed_and_quarantined(timestamp: object) -> None:
    decoded = decode_event_row((2, timestamp, "chat", "{}"))

    assert decoded.ts == 0.0
    assert decoded.corruption is not None
    assert CorruptEventReason.INVALID_TIMESTAMP in decoded.corruption.reasons


@pytest.mark.parametrize("kind", [None, b"chat", "", "bad\nkind", "x" * 129])
def test_unsafe_kind_is_not_reflected_in_the_marker(kind: object) -> None:
    decoded = decode_event_row((3, 1.0, kind, "{}"))

    assert decoded.kind == CORRUPT_EVENT_KIND
    assert decoded.corruption is not None
    assert decoded.corruption.original_kind is None
    assert CorruptEventReason.INVALID_KIND in decoded.corruption.reasons


def test_reserved_marker_kind_cannot_be_forged_in_sqlite() -> None:
    decoded = decode_event_row((4, 1.0, CORRUPT_EVENT_KIND, "{}"))

    assert decoded.corruption is not None
    assert decoded.corruption.original_kind == CORRUPT_EVENT_KIND
    assert decoded.corruption.reasons == (CorruptEventReason.RESERVED_KIND,)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"\xff", CorruptEventReason.INVALID_UTF8),
        (42, CorruptEventReason.PAYLOAD_NOT_TEXT),
        (None, CorruptEventReason.PAYLOAD_NOT_TEXT),
        ("{", CorruptEventReason.INVALID_JSON),
        ("[]", CorruptEventReason.PAYLOAD_NOT_OBJECT),
    ],
)
def test_malformed_payload_becomes_a_safe_marker(
    payload: object, reason: CorruptEventReason
) -> None:
    decoded = decode_event_row((5, 1.0, "claim", payload))

    assert decoded.kind == CORRUPT_EVENT_KIND
    assert decoded.corruption is not None
    assert reason in decoded.corruption.reasons
    assert payload not in decoded.payload.values()
    assert len(decoded.corruption.payload_sha256) == 64


def test_valid_utf8_blob_is_accepted_as_json_text() -> None:
    decoded = decode_event_row((6, 1, "chat", b'{"payload":"ok"}'))
    assert decoded.payload == {"payload": "ok"}
    assert decoded.corruption is None


def test_recursive_json_failure_is_quarantined(monkeypatch: pytest.MonkeyPatch) -> None:
    def _recurse(_text: str) -> Any:
        raise RecursionError

    monkeypatch.setattr(recovery.json, "loads", _recurse)
    decoded = decode_event_row((8, 1.0, "chat", "{}"))
    assert decoded.corruption is not None
    assert decoded.corruption.reasons == (CorruptEventReason.INVALID_JSON,)


def test_payload_digests_are_sqlite_type_domain_separated() -> None:
    values = [None, b"1", "1", True, 1, 1.0, object()]
    digests = {recovery._payload_digest(value) for value in values}
    assert len(digests) == len(values) - 1
    assert recovery._payload_digest(True) == recovery._payload_digest(1)


def test_marker_round_trips_through_strict_parser() -> None:
    marker = decode_event_row((9, 1.0, "claim", "not-json")).corruption
    assert marker is not None
    assert CorruptEventRow.from_payload(9, marker.as_payload()) == marker


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 99}, "schema"),
        ({"schema_version": True}, "schema"),
        ({"seq": 10}, "sequence"),
        ({"seq": True}, "sequence"),
        ({"seq": 9.0}, "sequence"),
        ({"original_kind": "bad\nkind"}, "original kind"),
        ({"reasons": []}, "no reasons"),
        ({"reasons": ["unknown"]}, "reason"),
        ({"payload_sha256": "xyz"}, "digest"),
        ({"payload_sha256": "G" * 64}, "digest"),
    ],
)
def test_marker_parser_rejects_tampering(change: dict[str, object], message: str) -> None:
    marker = decode_event_row((9, 1.0, "chat", "bad")).corruption
    assert marker is not None
    payload = marker.as_payload()
    payload.update(change)

    with pytest.raises(ValueError, match=message):
        CorruptEventRow.from_payload(9, payload)


def test_decoder_rejects_wrong_row_shape() -> None:
    with pytest.raises(ValueError, match="must contain"):
        decode_event_row((1, 2, 3))


def test_json_value_error_is_quarantined(monkeypatch: pytest.MonkeyPatch) -> None:
    def _too_large(_text: str) -> Any:
        raise ValueError("integer string conversion limit")

    monkeypatch.setattr(recovery.json, "loads", _too_large)
    decoded = decode_event_row((1, 1.0, "chat", "{}"))
    assert decoded.corruption is not None
    assert decoded.corruption.reasons == (CorruptEventReason.INVALID_JSON,)


@pytest.mark.parametrize("sequence", [True, "1"])
def test_decoder_rejects_non_integer_primary_key(sequence: object) -> None:
    with pytest.raises(ValueError, match="sequence"):
        decode_event_row((sequence, 1.0, "chat", "{}"))
