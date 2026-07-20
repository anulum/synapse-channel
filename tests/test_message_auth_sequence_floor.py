# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — REV-SEC-07 durable sequence-floor and restart tests
"""Restart, skew, failure, and compatibility tests for REV-SEC-07."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.message_auth import (
    MessageAuthKey,
    MessageReplayCache,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import (
    DurableAdmitResult,
    DurableMessageAuthReplayStore,
    SequenceFloorMode,
)
from synapse_channel.core.protocol import build_envelope

_SECRET = b"rev-sec-07-shared-secret"
_SENDER = "ALPHA"
_KEY = MessageAuthKey(key_id="main", secret=_SECRET, senders=frozenset({_SENDER}))


def _signed(*, nonce: str, sequence: int, timestamp: float) -> dict[str, object]:
    frame = build_envelope(_SENDER, "claim", target="System", task_id="T1", now=timestamp)
    return sign_frame(
        frame,
        key=_KEY,
        nonce=nonce,
        sequence=sequence,
        timestamp=timestamp,
    )


def _verify(
    frame: dict[str, object], cache: MessageReplayCache, *, now: float
) -> VerificationResult:
    return verify_frame(
        frame,
        keys={_KEY.key_id: _KEY},
        replay_cache=cache,
        now=now,
        required_sender=_SENDER,
    )


def test_present_semantics_sequence_is_not_floor_without_mode() -> None:
    """Default mode keeps sequence as metadata: lower sequence with new nonce is ok."""
    cache = MessageReplayCache(window_seconds=30.0, max_entries=16)
    assert (
        _verify(_signed(nonce="n1", sequence=5, timestamp=100.0), cache, now=100.0)
        == VerificationResult.OK
    )
    assert (
        _verify(_signed(nonce="n2", sequence=1, timestamp=100.5), cache, now=100.5)
        == VerificationResult.OK
    )


def test_present_semantics_nonce_is_replay_identity() -> None:
    cache = MessageReplayCache(window_seconds=30.0, max_entries=16)
    frame = _signed(nonce="same", sequence=1, timestamp=100.0)
    assert _verify(frame, cache, now=100.0) == VerificationResult.OK
    assert _verify(frame, cache, now=100.1) == VerificationResult.REPLAYED


def test_skew_past_window_expires_before_admission() -> None:
    cache = MessageReplayCache(window_seconds=10.0, max_entries=16, future_skew_seconds=1.0)
    frame = _signed(nonce="old", sequence=1, timestamp=100.0)
    assert _verify(frame, cache, now=110.001) == VerificationResult.EXPIRED


def test_skew_future_beyond_allowance_expires() -> None:
    cache = MessageReplayCache(window_seconds=10.0, max_entries=16, future_skew_seconds=1.0)
    frame = _signed(nonce="future", sequence=1, timestamp=102.0)
    assert _verify(frame, cache, now=100.0) == VerificationResult.EXPIRED


def test_skew_within_future_allowance_admits() -> None:
    cache = MessageReplayCache(window_seconds=10.0, max_entries=16, future_skew_seconds=1.0)
    frame = _signed(nonce="near-future", sequence=1, timestamp=100.5)
    assert _verify(frame, cache, now=100.0) == VerificationResult.OK


def test_durable_nonce_survives_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "message-auth-replay.sqlite"
    first = DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0)
    cache_a = MessageReplayCache(
        window_seconds=30.0,
        max_entries=64,
        durable=first,
        sequence_floor_mode=SequenceFloorMode.OFF,
    )
    frame = _signed(nonce="restart-nonce", sequence=3, timestamp=200.0)
    assert _verify(frame, cache_a, now=200.0) == VerificationResult.OK
    first.close()

    second = DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0)
    cache_b = MessageReplayCache(
        window_seconds=30.0,
        max_entries=64,
        durable=second,
        sequence_floor_mode=SequenceFloorMode.OFF,
    )
    assert _verify(frame, cache_b, now=200.5) == VerificationResult.REPLAYED
    second.close()


def test_without_durable_restart_reopens_nonce(tmp_path: Path) -> None:
    """Document present residual: process-local cache alone loses nonce memory."""
    del tmp_path
    cache_a = MessageReplayCache(window_seconds=30.0, max_entries=16)
    frame = _signed(nonce="volatile", sequence=1, timestamp=50.0)
    assert _verify(frame, cache_a, now=50.0) == VerificationResult.OK
    cache_b = MessageReplayCache(window_seconds=30.0, max_entries=16)
    assert _verify(frame, cache_b, now=50.5) == VerificationResult.OK


def test_compat_mode_allows_sequence_reset_with_new_nonce(tmp_path: Path) -> None:
    path = tmp_path / "compat.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0) as store:
        cache = MessageReplayCache(
            window_seconds=30.0,
            max_entries=64,
            durable=store,
            sequence_floor_mode=SequenceFloorMode.COMPAT,
        )
        assert (
            _verify(_signed(nonce="c1", sequence=10, timestamp=300.0), cache, now=300.0)
            == VerificationResult.OK
        )
        assert store.floor("main", _SENDER) == 10
        assert (
            _verify(_signed(nonce="c2", sequence=1, timestamp=300.5), cache, now=300.5)
            == VerificationResult.OK
        )
        # Floor still advances only when sequence is higher.
        assert store.floor("main", _SENDER) == 10


def test_compat_mode_same_nonce_still_replayed(tmp_path: Path) -> None:
    path = tmp_path / "compat-replay.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0) as store:
        cache = MessageReplayCache(
            window_seconds=30.0,
            max_entries=64,
            durable=store,
            sequence_floor_mode=SequenceFloorMode.COMPAT,
        )
        frame = _signed(nonce="c-same", sequence=2, timestamp=400.0)
        assert _verify(frame, cache, now=400.0) == VerificationResult.OK
        assert _verify(frame, cache, now=400.1) == VerificationResult.REPLAYED


def test_strict_mode_rejects_sequence_at_or_below_floor(tmp_path: Path) -> None:
    path = tmp_path / "strict.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0) as store:
        cache = MessageReplayCache(
            window_seconds=30.0,
            max_entries=64,
            durable=store,
            sequence_floor_mode=SequenceFloorMode.STRICT,
        )
        assert (
            _verify(_signed(nonce="s1", sequence=7, timestamp=500.0), cache, now=500.0)
            == VerificationResult.OK
        )
        assert (
            _verify(_signed(nonce="s2", sequence=7, timestamp=500.1), cache, now=500.1)
            == VerificationResult.SEQUENCE_MISMATCH
        )
        assert (
            _verify(_signed(nonce="s3", sequence=6, timestamp=500.2), cache, now=500.2)
            == VerificationResult.SEQUENCE_MISMATCH
        )
        assert (
            _verify(_signed(nonce="s4", sequence=8, timestamp=500.3), cache, now=500.3)
            == VerificationResult.OK
        )


def test_strict_mode_floor_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "strict-restart.sqlite"
    store_a = DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0)
    cache_a = MessageReplayCache(
        window_seconds=30.0,
        max_entries=64,
        durable=store_a,
        sequence_floor_mode=SequenceFloorMode.STRICT,
    )
    assert (
        _verify(_signed(nonce="sr1", sequence=4, timestamp=600.0), cache_a, now=600.0)
        == VerificationResult.OK
    )
    store_a.close()

    store_b = DurableMessageAuthReplayStore(path, max_entries=64, window_seconds=30.0)
    cache_b = MessageReplayCache(
        window_seconds=30.0,
        max_entries=64,
        durable=store_b,
        sequence_floor_mode=SequenceFloorMode.STRICT,
    )
    assert (
        _verify(_signed(nonce="sr2", sequence=4, timestamp=600.5), cache_b, now=600.5)
        == VerificationResult.SEQUENCE_MISMATCH
    )
    assert (
        _verify(_signed(nonce="sr3", sequence=5, timestamp=600.6), cache_b, now=600.6)
        == VerificationResult.OK
    )
    store_b.close()


def test_strict_in_memory_floor_without_durable() -> None:
    cache = MessageReplayCache(
        window_seconds=30.0,
        max_entries=16,
        sequence_floor_mode=SequenceFloorMode.STRICT,
    )
    assert (
        _verify(_signed(nonce="m1", sequence=2, timestamp=10.0), cache, now=10.0)
        == VerificationResult.OK
    )
    assert (
        _verify(_signed(nonce="m2", sequence=2, timestamp=10.1), cache, now=10.1)
        == VerificationResult.SEQUENCE_MISMATCH
    )


def test_capacity_full_refuses_new_nonce(tmp_path: Path) -> None:
    path = tmp_path / "capacity.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=2, window_seconds=30.0) as store:
        assert (
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="a",
                sequence=1,
                timestamp=1.0,
                now=1.0,
            )
            is DurableAdmitResult.ACCEPTED
        )
        assert (
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="b",
                sequence=2,
                timestamp=1.1,
                now=1.1,
            )
            is DurableAdmitResult.ACCEPTED
        )
        assert (
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="c",
                sequence=3,
                timestamp=1.2,
                now=1.2,
            )
            is DurableAdmitResult.CAPACITY
        )


def test_window_eviction_reopens_capacity(tmp_path: Path) -> None:
    path = tmp_path / "evict.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=1, window_seconds=5.0) as store:
        assert (
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="old",
                sequence=1,
                timestamp=0.0,
                now=0.0,
            )
            is DurableAdmitResult.ACCEPTED
        )
        assert (
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="new",
                sequence=2,
                timestamp=10.0,
                now=10.0,
            )
            is DurableAdmitResult.ACCEPTED
        )
        assert store.nonce_count() == 1


def test_failure_closed_on_closed_store(tmp_path: Path) -> None:
    path = tmp_path / "closed.sqlite"
    store = DurableMessageAuthReplayStore(path, max_entries=8, window_seconds=30.0)
    store.close()
    cache = MessageReplayCache(
        window_seconds=30.0,
        max_entries=8,
        durable=store,
        sequence_floor_mode=SequenceFloorMode.OFF,
    )
    assert (
        _verify(_signed(nonce="after-close", sequence=1, timestamp=1.0), cache, now=1.0)
        == VerificationResult.REPLAYED
    )


def test_invalid_sequence_rejected_by_store(tmp_path: Path) -> None:
    path = tmp_path / "bad-seq.sqlite"
    with DurableMessageAuthReplayStore(path, max_entries=8, window_seconds=30.0) as store:
        with pytest.raises(ValueError):
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="x",
                sequence=0,
                timestamp=1.0,
                now=1.0,
            )
        with pytest.raises(ValueError):
            store.admit(
                key_id="main",
                sender=_SENDER,
                nonce="y",
                sequence=True,  # bool must fail closed like non-int
                timestamp=1.0,
                now=1.0,
            )


def test_concurrent_same_nonce_only_one_accepts(tmp_path: Path) -> None:
    """Two connections cannot both accept the same durable nonce."""
    path = tmp_path / "race.sqlite"
    left = DurableMessageAuthReplayStore(path, max_entries=16, window_seconds=30.0)
    right = DurableMessageAuthReplayStore(path, max_entries=16, window_seconds=30.0)
    first = left.admit(
        key_id="main",
        sender=_SENDER,
        nonce="race",
        sequence=1,
        timestamp=1.0,
        now=1.0,
    )
    second = right.admit(
        key_id="main",
        sender=_SENDER,
        nonce="race",
        sequence=1,
        timestamp=1.0,
        now=1.0,
    )
    left.close()
    right.close()
    outcomes = {first, second}
    assert DurableAdmitResult.ACCEPTED in outcomes
    assert DurableAdmitResult.REPLAYED in outcomes
