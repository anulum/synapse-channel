# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for per-message authentication primitives
"""Per-message authentication primitive tests."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    SignedEventVerificationResult,
    VerificationResult,
    canonical_event_frame,
    canonical_frame,
    sign_event_frame,
    sign_frame,
    verify_event_signature,
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

    assert (
        verify_frame(
            signed,
            keys={key.key_id: key},
            replay_cache=replay,
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.OK
    )
    assert (
        verify_frame(
            signed,
            keys={key.key_id: key},
            replay_cache=replay,
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.REPLAYED
    )


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

    assert (
        verify_frame(
            first,
            keys={key.key_id: key},
            replay_cache=replay,
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.OK
    )
    assert (
        verify_frame(
            second,
            keys={key.key_id: key},
            replay_cache=replay,
            now=100.0,
            required_sender="ALPHA",
        )
        == VerificationResult.OK
    )


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


def test_sign_and_verify_ed25519_event_signature_with_replay_surface() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="SYNAPSE-CHANNEL:main:2026-06",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"SYNAPSE-CHANNEL"}),
    )
    trust = EventSignatureTrustBundle(
        keys={key.key_id: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    frame = build_envelope(
        "ALPHA",
        "claim",
        target="System",
        task_id="T1",
        project="SYNAPSE-CHANNEL",
        now=12.0,
    )

    signed = sign_event_frame(
        frame,
        key_id=key.key_id,
        private_key=private_key,
        nonce="event-nonce-1",
        sequence=1,
        signed_at=100.0,
    )

    assert signed["signature"]["algorithm"] == "ed25519"
    assert (
        verify_event_signature(
            signed,
            trust_bundle=trust,
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.VALID
    )
    assert (
        verify_event_signature(
            signed,
            trust_bundle=trust,
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.REPLAYED
    )


def test_event_signature_reports_bad_signature_scope_and_revocation_failures() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="SYNAPSE-CHANNEL:main:2026-06",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"SYNAPSE-CHANNEL"}),
    )
    signed = sign_event_frame(
        build_envelope(
            "ALPHA",
            "claim",
            target="System",
            task_id="T1",
            project="SYNAPSE-CHANNEL",
            now=12.0,
        ),
        key_id=key.key_id,
        private_key=private_key,
        nonce="event-nonce-1",
        sequence=1,
        signed_at=100.0,
    )

    def fresh_trust(*, revoked: bool = False) -> EventSignatureTrustBundle:
        return EventSignatureTrustBundle(
            keys={key.key_id: key.with_revoked(revoked)},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
        )

    assert (
        verify_event_signature(
            signed | {"task_id": "T2"},
            trust_bundle=fresh_trust(),
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.BAD_SIGNATURE
    )
    assert (
        verify_event_signature(
            signed,
            trust_bundle=fresh_trust(),
            now=100.0,
            required_sender="BETA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.SENDER_MISMATCH
    )
    assert (
        verify_event_signature(
            signed,
            trust_bundle=fresh_trust(),
            now=100.0,
            required_sender="ALPHA",
            required_project="OTHER",
        )
        == SignedEventVerificationResult.PROJECT_SCOPE_MISMATCH
    )
    assert (
        verify_event_signature(
            signed,
            trust_bundle=fresh_trust(revoked=True),
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.REVOKED_KEY
    )


def test_event_signature_reports_missing_unknown_expired_and_malformed_metadata() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="SYNAPSE-CHANNEL:main:2026-06",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"SYNAPSE-CHANNEL"}),
        expires_at=99.0,
    )
    signed = sign_event_frame(
        build_envelope(
            "ALPHA",
            "claim",
            target="System",
            task_id="T1",
            project="SYNAPSE-CHANNEL",
            now=12.0,
        ),
        key_id=key.key_id,
        private_key=private_key,
        nonce="event-nonce-1",
        sequence=1,
        signed_at=100.0,
    )
    trust = EventSignatureTrustBundle(
        keys={key.key_id: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )

    assert b'"signature":"opaque"' in canonical_event_frame(signed | {"signature": "opaque"})
    assert (
        verify_event_signature(
            build_envelope("ALPHA", "claim", target="System", task_id="T1", now=12.0),
            trust_bundle=trust,
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.MISSING_SIGNATURE
    )
    assert (
        verify_event_signature(
            signed | {"signature": signed["signature"] | {"key_id": "missing"}},
            trust_bundle=trust,
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.UNKNOWN_KEY
    )
    assert (
        verify_event_signature(
            signed,
            trust_bundle=trust,
            now=100.0,
            required_sender="ALPHA",
            required_project="SYNAPSE-CHANNEL",
        )
        == SignedEventVerificationResult.EXPIRED
    )

    valid_key = EventSignatureKey(
        key_id=key.key_id,
        public_key=key.public_key,
        senders=key.senders,
        projects=key.projects,
    )
    valid_trust = EventSignatureTrustBundle(
        keys={valid_key.key_id: valid_key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    malformed_frames = (
        signed | {"signature": signed["signature"] | {"algorithm": "rsa"}},
        signed | {"signature": signed["signature"] | {"signed_at": "bad"}},
        signed | {"signature": signed["signature"] | {"sequence": 0}},
        signed | {"signature": signed["signature"] | {"signed_at": 10.0}},
        signed | {"signature": signed["signature"] | {"nonce": ""}},
        signed | {"signature": signed["signature"] | {"value": "not-base64!!"}},
    )
    expected = (
        SignedEventVerificationResult.BAD_SIGNATURE,
        SignedEventVerificationResult.BAD_SIGNATURE,
        SignedEventVerificationResult.SEQUENCE_MISMATCH,
        SignedEventVerificationResult.EXPIRED,
        SignedEventVerificationResult.BAD_SIGNATURE,
        SignedEventVerificationResult.BAD_SIGNATURE,
    )
    for frame, result in zip(malformed_frames, expected, strict=True):
        assert (
            verify_event_signature(
                frame,
                trust_bundle=valid_trust,
                now=100.0,
                required_sender="ALPHA",
                required_project="SYNAPSE-CHANNEL",
            )
            == result
        )
