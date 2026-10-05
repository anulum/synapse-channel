# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — schema, own-side and retry-key rules of native-message records
"""Pin the bounded wire schema of a native-message record and its two pure rules."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from synapse_channel.core.native_message import (
    MAX_NATIVE_TEXT_BYTES,
    NativeMessageError,
    native_idempotency_key,
    own_side_seat,
    parse_native_message_record,
)

TEXT = "Správa č. 1 — first line.\nSecond line."
SENDER = "GROUP-A/claude-aaaa"
RECIPIENT = "GROUP-B/claude-bbbb"


def frame(**changes: Any) -> dict[str, Any]:
    """Return a valid sender-side outcome frame with ``changes`` applied."""
    encoded = TEXT.encode("utf-8")
    data: dict[str, Any] = {
        "sender": SENDER,
        "type": "native_message_record",
        "idem_key": "nm-test",
        "channel": "claude_cross_session",
        "direction": "sent",
        "phase": "outcome",
        "outcome": "queued",
        "sender_seat": SENDER,
        "recipient_seat": RECIPIENT,
        "sender_native_session": "11111111-2222-4333-8444-555555555555",
        "recipient_address": "session-b1",
        "native_message_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "native_call_id": "toolu_01",
        "sent_at": "2026-10-05T09:50:22.130036Z",
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": TEXT,
        "tool_result": {"success": True},
    }
    data.update(changes)
    return data


def test_valid_record_keeps_every_field_and_drops_transport_fields() -> None:
    record = parse_native_message_record(frame())
    assert record == {
        "channel": "claude_cross_session",
        "direction": "sent",
        "phase": "outcome",
        "outcome": "queued",
        "sender_seat": SENDER,
        "recipient_seat": RECIPIENT,
        "sender_native_session": "11111111-2222-4333-8444-555555555555",
        "recipient_native_session": None,
        "recipient_address": "session-b1",
        "execution_host": None,
        "native_message_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "native_call_id": "toolu_01",
        "source_msg_seq": None,
        "sent_at": "2026-10-05T09:50:22.130036Z",
        "text_sha256": hashlib.sha256(TEXT.encode("utf-8")).hexdigest(),
        "text_bytes": len(TEXT.encode("utf-8")),
        "text": TEXT,
        "tool_result": {"success": True},
    }


def test_hash_only_record_and_attempt_phase_are_valid() -> None:
    record = parse_native_message_record(
        frame(text=None, text_bytes=200_000, phase="attempt", outcome=None, source_msg_seq=7)
    )
    assert record["text"] is None
    assert record["text_bytes"] == 200_000
    assert record["outcome"] is None
    assert record["source_msg_seq"] == 7


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channel", "telepathy"),
        ("channel", None),
        ("direction", "both"),
        ("phase", "later"),
        ("outcome", "delivered"),
        ("outcome", None),
        ("sender_seat", ""),
        ("sender_seat", "x" * 257),
        ("recipient_seat", 7),
        ("recipient_address", "a\nb"),
        ("execution_host", "host\x00"),
        ("native_message_id", ["id"]),
        ("sent_at", "2026-10-05 09:50:22"),
        ("sent_at", "2026-10-05T09:50:22+02:00"),
        ("sent_at", 1791194422),
        ("source_msg_seq", 0),
        ("source_msg_seq", True),
        ("source_msg_seq", "7"),
        ("text_sha256", "A" * 64),
        ("text_sha256", "short"),
        ("text_bytes", True),
        ("text_bytes", 0),
        ("text_bytes", 16_777_217),
        ("text", 5),
        ("tool_result", {"x": float("nan")}),
        ("tool_result", "r" * 5_000),
    ],
)
def test_malformed_field_is_refused(field: str, value: object) -> None:
    with pytest.raises(NativeMessageError) as refused:
        parse_native_message_record(frame(**{field: value}))
    assert refused.value.reason == "native_record_invalid"
    assert refused.value.code == "native_message"


def test_outcome_is_refused_on_an_attempt() -> None:
    with pytest.raises(NativeMessageError, match="phase outcome only"):
        parse_native_message_record(frame(phase="attempt"))


@pytest.mark.parametrize(
    "changes",
    [
        {"text": TEXT + "!"},
        {"text_bytes": len(TEXT.encode("utf-8")) + 1},
        {"text_sha256": hashlib.sha256(b"another text").hexdigest()},
    ],
)
def test_inline_text_must_match_its_digest_and_size(changes: dict[str, Any]) -> None:
    with pytest.raises(NativeMessageError) as refused:
        parse_native_message_record(frame(**changes))
    assert refused.value.reason == "native_record_text_mismatch"


def test_inline_text_above_the_bound_is_refused_and_its_hash_only_form_accepted() -> None:
    text = "ž" * (MAX_NATIVE_TEXT_BYTES // 2 + 1)
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    with pytest.raises(NativeMessageError) as refused:
        parse_native_message_record(frame(text=text, text_bytes=len(encoded), text_sha256=digest))
    assert refused.value.reason == "native_record_text_too_large"
    record = parse_native_message_record(
        frame(text=None, text_bytes=len(encoded), text_sha256=digest)
    )
    assert (record["text_sha256"], record["text_bytes"]) == (digest, len(encoded))


def test_lone_surrogate_in_text_is_hashed_not_crashed() -> None:
    text = "broken \ud800 surrogate"
    encoded = text.encode("utf-8", errors="surrogatepass")
    record = parse_native_message_record(
        frame(
            text=text,
            text_bytes=len(encoded),
            text_sha256=hashlib.sha256(encoded).hexdigest(),
        )
    )
    assert record["text"] == text


def test_own_side_is_the_sender_of_a_sent_and_the_recipient_of_a_received_record() -> None:
    sent = parse_native_message_record(frame())
    received = parse_native_message_record(frame(direction="received"))
    assert own_side_seat(sent) == SENDER
    assert own_side_seat(received) == RECIPIENT
    assert own_side_seat(parse_native_message_record(frame(sender_seat=None))) is None
    unresolved = parse_native_message_record(frame(direction="received", recipient_seat=None))
    assert own_side_seat(unresolved) is None


def test_retry_key_is_stable_and_separates_phase_side_channel_and_message() -> None:
    outcome = parse_native_message_record(frame())
    key = native_idempotency_key(outcome)
    assert key == native_idempotency_key(parse_native_message_record(frame()))
    assert key.startswith("nm-")
    assert len(key) == 67
    variants = [
        frame(phase="attempt", outcome=None),
        frame(direction="received"),
        frame(channel="codex_queue"),
        frame(native_message_id="00000000-0000-4000-8000-000000000001"),
    ]
    keys = {native_idempotency_key(parse_native_message_record(item)) for item in variants}
    assert len(keys | {key}) == 5


def test_retry_key_without_a_native_id_uses_session_call_time_and_digest() -> None:
    base = parse_native_message_record(frame(native_message_id=None))
    assert native_idempotency_key(base) == native_idempotency_key(
        parse_native_message_record(frame(native_message_id=None, tool_result=None))
    )
    changed = [
        frame(native_message_id=None, native_call_id="toolu_02"),
        frame(native_message_id=None, sent_at="2026-10-05T09:50:23Z"),
        frame(native_message_id=None, sender_native_session="another-session"),
    ]
    keys = {native_idempotency_key(parse_native_message_record(item)) for item in changed}
    assert native_idempotency_key(base) not in keys
    assert len(keys) == 3


def test_attempt_and_outcome_of_one_message_never_share_a_key() -> None:
    attempt = parse_native_message_record(frame(phase="attempt", outcome=None))
    outcome = parse_native_message_record(frame())
    assert native_idempotency_key(attempt) != native_idempotency_key(outcome)
    attempt_without_id = parse_native_message_record(
        frame(phase="attempt", outcome=None, native_message_id=None)
    )
    assert native_idempotency_key(attempt) == native_idempotency_key(attempt_without_id)
