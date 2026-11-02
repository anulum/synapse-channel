# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — replay defence edges: a full durable ledger, a signed-event sequence floor
"""A full durable nonce ledger refuses new frames; a signed event below its floor is refused."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    SignedEventVerificationResult,
    VerificationResult,
    sign_event_frame,
    sign_frame,
    verify_event_signature,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protocol import build_envelope

_SENDER = "ALPHA"
_KEY = MessageAuthKey(key_id="main", secret=b"capacity-secret", senders=frozenset({_SENDER}))


def _verify(cache: MessageReplayCache, nonce: str, at: float) -> VerificationResult:
    frame = build_envelope(_SENDER, "claim", target="System", task_id="T1", now=at)
    signed = sign_frame(frame, key=_KEY, nonce=nonce, sequence=1, timestamp=at)
    return verify_frame(
        signed, keys={_KEY.key_id: _KEY}, replay_cache=cache, now=at, required_sender=_SENDER
    )


def test_a_full_durable_ledger_refuses_new_nonces_until_the_window_passes(
    tmp_path: Path,
) -> None:
    """At capacity the durable decision fails closed as replayed, even with memory to spare."""
    with DurableMessageAuthReplayStore(
        tmp_path / "replay.sqlite", max_entries=1, window_seconds=30.0
    ) as store:
        cache = MessageReplayCache(window_seconds=30.0, max_entries=64, durable=store)
        assert _verify(cache, "first", 100.0) == VerificationResult.OK
        assert _verify(cache, "second", 101.0) == VerificationResult.REPLAYED
        assert _verify(cache, "third", 140.0) == VerificationResult.OK


def test_a_signed_event_at_or_below_its_sequence_floor_is_refused() -> None:
    """With a strict floor, a fresh nonce cannot reuse or lower an accepted sequence."""
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="P:main", private_key=private_key, senders=frozenset({_SENDER})
    )
    trust = EventSignatureTrustBundle(
        keys={key.key_id: key},
        replay_cache=MessageReplayCache(
            window_seconds=30.0, max_entries=16, sequence_floor_mode=SequenceFloorMode.STRICT
        ),
    )

    def verify(nonce: str, sequence: int, at: float) -> SignedEventVerificationResult:
        frame = build_envelope(_SENDER, "claim", target="System", task_id="T1", now=at)
        signed = sign_event_frame(
            frame,
            key_id=key.key_id,
            private_key=private_key,
            nonce=nonce,
            sequence=sequence,
            signed_at=at,
        )
        return verify_event_signature(signed, trust_bundle=trust, now=at, required_sender=_SENDER)

    assert verify("n-1", 5, 100.0) == SignedEventVerificationResult.VALID
    assert verify("n-2", 5, 100.5) == SignedEventVerificationResult.SEQUENCE_MISMATCH
    assert verify("n-3", 4, 101.0) == SignedEventVerificationResult.SEQUENCE_MISMATCH
    assert verify("n-4", 6, 101.5) == SignedEventVerificationResult.VALID
