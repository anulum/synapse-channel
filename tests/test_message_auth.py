# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for per-message authentication primitives
"""Per-message authentication primitive tests."""

from __future__ import annotations

from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    canonical_frame,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.protocol import build_envelope


def test_canonical_frame_excludes_only_authentication_value() -> None:
    frame = build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0)
    frame["auth"] = {
        "alg": "hmac-sha256",
        "kid": "main",
        "nonce": "n1",
        "sequence": 1,
        "timestamp": 123.0,
        "value": "signature-to-drop",
    }

    canonical = canonical_frame(frame)

    assert b"signature-to-drop" not in canonical
    assert b'"kid":"main"' in canonical
    assert b'"sequence":1' in canonical
    assert b'"sender":"ALPHA"' in canonical


def test_canonical_frame_preserves_non_mapping_authentication() -> None:
    frame = build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0)
    frame["auth"] = "opaque"

    canonical = canonical_frame(frame)

    assert b'"auth":"opaque"' in canonical


def test_sign_and_verify_hmac_frame_records_replay_once() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    replay = MessageReplayCache(window_seconds=30.0, max_entries=16)
    frame = build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0)

    signed = sign_frame(
        frame,
        key=key,
        nonce="nonce-1",
        sequence=1,
        timestamp=100.0,
    )

    assert verify_frame(
        signed,
        keys={key.key_id: key},
        replay_cache=replay,
        now=100.0,
        required_sender="ALPHA",
    ) == VerificationResult.OK
    assert verify_frame(
        signed,
        keys={key.key_id: key},
        replay_cache=replay,
        now=100.0,
        required_sender="ALPHA",
    ) == VerificationResult.REPLAYED


def test_verify_frame_reports_sender_sequence_key_and_timestamp_failures() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
        key=key,
        nonce="nonce-1",
        sequence=1,
        timestamp=100.0,
    )

    assert (
        verify_frame(
            signed,
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="BETA",
        )
        == VerificationResult.SENDER_MISMATCH
    )
    assert (
        verify_frame(
            signed | {"auth": signed["auth"] | {"sequence": 0}},
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.SEQUENCE_MISMATCH
    )
    assert (
        verify_frame(
            signed | {"auth": signed["auth"] | {"kid": "missing"}},
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.UNKNOWN_KEY
    )
    assert (
        verify_frame(
            signed,
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=200.0,
            required_sender="ALPHA",
        )
        == VerificationResult.EXPIRED
    )
    assert (
        verify_frame(
            signed,
            keys={key.key_id: MessageAuthKey(key_id="main", secret=b"shared-secret")},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.SENDER_MISMATCH
    )


def test_verify_frame_allows_same_sequence_when_nonce_is_new() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    replay = MessageReplayCache(window_seconds=30.0, max_entries=16)
    first = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
        key=key,
        nonce="nonce-1",
        sequence=1,
        timestamp=100.0,
    )
    second = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T2", now=12.0),
        key=key,
        nonce="nonce-2",
        sequence=1,
        timestamp=100.0,
    )

    assert verify_frame(
        first,
        keys={key.key_id: key},
        replay_cache=replay,
        now=100.0,
        required_sender="ALPHA",
    ) == VerificationResult.OK
    assert verify_frame(
        second,
        keys={key.key_id: key},
        replay_cache=replay,
        now=100.0,
        required_sender="ALPHA",
    ) == VerificationResult.OK


def test_verify_frame_rejects_future_timestamp_beyond_skew() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
        key=key,
        nonce="nonce-1",
        sequence=1,
        timestamp=102.0,
    )

    assert (
        verify_frame(
            signed,
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.EXPIRED
    )


def test_verify_frame_reports_missing_revoked_and_malformed_authentication() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
        key=key,
        nonce="nonce-1",
        sequence=1,
        timestamp=100.0,
    )
    replay = MessageReplayCache(window_seconds=30.0, max_entries=16)

    assert (
        verify_frame(
            build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
            keys={key.key_id: key},
            replay_cache=replay,
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.MISSING
    )
    assert (
        verify_frame(
            signed,
            keys={key.key_id: MessageAuthKey(key_id="main", secret=b"shared-secret", revoked=True)},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.REVOKED_KEY
    )
    malformed_frames = (
        signed | {"auth": signed["auth"] | {"alg": "unknown"}},
        signed | {"auth": signed["auth"] | {"timestamp": "not-a-number"}},
        signed | {"auth": signed["auth"] | {"nonce": ""}},
        signed | {"auth": signed["auth"] | {"value": ""}},
        signed | {"payload": "tampered"},
    )
    for frame in malformed_frames:
        assert (
            verify_frame(
                frame,
                keys={key.key_id: key},
                replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
                now=100.0,
                required_sender="ALPHA",
            )
            == VerificationResult.BAD_AUTHENTICATION
        )


def test_replay_cache_evicts_by_window_and_rejects_capacity_pressure() -> None:
    cache = MessageReplayCache(window_seconds=10.0, max_entries=2)

    assert cache.remember("main", "A", "old", 1, timestamp=89.0, now=100.0) is True
    assert cache.remember("main", "A", "n1", 2, timestamp=99.0, now=100.0) is True
    assert cache.remember("main", "A", "n2", 3, timestamp=100.0, now=100.0) is True
    assert cache.remember("main", "A", "n1", 2, timestamp=99.0, now=100.0) is False
    assert cache.remember("main", "A", "n3", 4, timestamp=101.0, now=101.0) is False
    assert cache.remember("main", "A", "n2", 3, timestamp=100.0, now=101.0) is False
    assert cache.remember("main", "A", "n4", 5, timestamp=112.0, now=112.0) is True

    resized = MessageReplayCache(window_seconds=10.0, max_entries=3)
    assert resized.remember("main", "A", "a", 1, timestamp=100.0, now=100.0) is True
    assert resized.remember("main", "A", "b", 2, timestamp=100.0, now=100.0) is True
    assert resized.remember("main", "A", "c", 3, timestamp=100.0, now=100.0) is True
    resized.max_entries = 1
    assert resized.remember("main", "A", "d", 4, timestamp=100.0, now=100.0) is False
    assert resized.remember("main", "A", "a", 5, timestamp=100.0, now=100.0) is False
