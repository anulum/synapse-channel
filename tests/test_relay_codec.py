# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

from synapse_channel.relay import (
    LITE_KEYS,
    LITE_VERSION,
    decode_lite,
    encode_lite,
)


def test_encode_lite_uses_short_keys() -> None:
    raw = {
        "msg_id": 42,
        "type": "chat",
        "sender": "USER",
        "target": "all",
        "payload": "hello",
        "timestamp": 1700000000.123,
        "hub_id": "syn-abc",
    }
    packed = encode_lite(raw)
    assert set(packed.keys()) == {"v", "i", "ty", "s", "to", "p", "t", "h", "c"}
    assert packed["v"] == LITE_VERSION
    assert packed["i"] == 42
    assert packed["p"] == "hello"
    assert packed["t"] == int(1700000000.123 * 1000.0)
    assert packed["h"] == "syn-abc"
    assert packed["c"] == ""


def test_encode_lite_short_keys_match_the_shared_schema() -> None:
    # The codec advertises its key set; encode must emit exactly those (plus v).
    packed = encode_lite({"msg_id": 1})
    assert set(LITE_KEYS.values()) | {"v"} == set(packed.keys())


def test_encode_lite_falls_back_on_bad_timestamp_and_id() -> None:
    before_ms = int(__import__("time").time() * 1000.0)
    packed = encode_lite({"timestamp": "not-a-number", "msg_id": "nope"})
    assert packed["i"] == 0
    assert packed["t"] >= before_ms
    assert packed["ty"] == "chat"
    assert packed["s"] == "?"


def test_encode_lite_defaults_when_id_missing() -> None:
    packed = encode_lite({"timestamp": 1.0})
    assert packed["i"] == 0


def test_decode_lite_falls_back_on_non_finite_timestamp_and_id() -> None:
    # A corrupted log entry with a non-finite ``t``/``i`` (``int(inf)`` raises
    # OverflowError, ``int(nan)`` raises ValueError) decodes to zero rather than
    # raising out of the reader.
    message = decode_lite({"t": float("inf"), "i": float("nan"), "s": "A", "p": "hi"})
    assert message["timestamp"] == 0.0
    assert message["msg_id"] == 0
    assert message["sender"] == "A"


def test_encode_lite_uses_now_when_timestamp_absent() -> None:
    before_ms = int(__import__("time").time() * 1000.0)
    packed = encode_lite({})
    assert packed["t"] >= before_ms


def test_decode_lite_inverts_encode_to_millisecond_precision() -> None:
    original = {
        "msg_id": 7,
        "type": "claim_granted",
        "sender": "SynapseHub",
        "target": "FAST",
        "payload": "granted H1",
        "timestamp": 1700000000.125,
        "hub_id": "syn-xyz",
    }
    restored = decode_lite(encode_lite(original))
    assert restored == {
        "sender": "SynapseHub",
        "target": "FAST",
        "type": "claim_granted",
        "payload": "granted H1",
        "timestamp": 1700000000.125,
        "msg_id": 7,
        "hub_id": "syn-xyz",
    }


def test_decode_lite_preserves_channel_id() -> None:
    restored = decode_lite(
        encode_lite(
            {
                "msg_id": 8,
                "type": "chat",
                "sender": "alice",
                "target": "all",
                "payload": "private",
                "timestamp": 1700000000.125,
                "hub_id": "syn-xyz",
                "channel": "ops",
            }
        )
    )

    assert restored["channel"] == "ops"


def test_decode_lite_uses_defaults_for_missing_and_malformed_keys() -> None:
    restored = decode_lite({"t": "bad", "i": "bad"})
    assert restored == {
        "sender": "?",
        "target": "all",
        "type": "chat",
        "payload": "",
        "timestamp": 0.0,
        "msg_id": 0,
        "hub_id": "",
    }
