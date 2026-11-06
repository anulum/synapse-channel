# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — connection-bound frame signatures
"""Exercise signing and checking a frame for one connection of one hub."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.frame_canonical import FrameCanonicalError, frame_canonical_json
from synapse_channel.core.frame_signature import (
    FRAME_SIGNATURE_DOMAIN,
    FRAME_SIGNATURE_FIELD,
    FrameSignatureResult,
    frame_signing_bytes,
    sign_session_frame,
    verify_session_frame,
)
from synapse_channel.core.message_auth import sign_event_frame

KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()
FRAME: dict[str, Any] = {
    "sender": "P/a",
    "type": "claim",
    "target": "System",
    "task_id": "t-ünïcode",
    "paths": ["src/x.py"],
}


def _signed(seq: int = 1) -> dict[str, Any]:
    return sign_session_frame(
        FRAME, key_id="k", private_key=KEY, hub_id="hub-1", session_nonce="n-1", seq=seq
    )


def _check(frame: dict[str, Any], **overrides: Any) -> tuple[FrameSignatureResult, int]:
    fields: dict[str, Any] = {
        "public_key": KEY.public_key(),
        "key_id": "k",
        "hub_id": "hub-1",
        "session_nonce": "n-1",
        "last_seq": 0,
    }
    fields.update(overrides)
    return verify_session_frame(frame, **fields)


def test_a_signed_frame_verifies_and_reports_its_sequence() -> None:
    signed = _signed(seq=7)
    assert _check(signed) == (FrameSignatureResult.VALID, 7)
    assert _check(signed, last_seq=6) == (FrameSignatureResult.VALID, 7)
    envelope = signed[FRAME_SIGNATURE_FIELD]
    assert set(envelope) == {
        "version",
        "algorithm",
        "key_id",
        "hub_id",
        "session_nonce",
        "seq",
        "value",
    }
    assert FRAME_SIGNATURE_FIELD not in FRAME
    assert frame_signing_bytes(signed).startswith(FRAME_SIGNATURE_DOMAIN)
    # An unsigned frame is covered by its RFC 8785 bytes behind the domain prefix.
    assert frame_signing_bytes(FRAME) == FRAME_SIGNATURE_DOMAIN + frame_canonical_json(FRAME)


def test_the_connection_binding_refuses_copies_and_replays() -> None:
    signed = _signed(seq=3)
    assert _check(signed, last_seq=3) == (FrameSignatureResult.REPLAYED, 3)
    assert _check(signed, last_seq=9) == (FrameSignatureResult.REPLAYED, 9)
    assert _check(signed, hub_id="hub-2")[0] is FrameSignatureResult.WRONG_HUB
    assert _check(signed, session_nonce="n-2")[0] is FrameSignatureResult.WRONG_SESSION
    assert _check(signed, key_id="k2")[0] is FrameSignatureResult.WRONG_KEY
    assert _check(signed, public_key=OTHER.public_key())[0] is FrameSignatureResult.BAD_SIGNATURE


@pytest.mark.parametrize(
    "change",
    [
        lambda frame: frame.update(type="release"),
        lambda frame: frame.update(task_id="t-other"),
        lambda frame: frame["paths"].append("src/y.py"),
        lambda frame: frame.update(extra=True),
        lambda frame: frame[FRAME_SIGNATURE_FIELD].update(seq=99),
        lambda frame: frame[FRAME_SIGNATURE_FIELD].update(value="AAAA"),
        lambda frame: frame[FRAME_SIGNATURE_FIELD].update(value="not base64!"),
    ],
)
def test_any_change_breaks_the_signature(change: Any) -> None:
    signed = _signed()
    tampered = copy.deepcopy(signed)
    change(tampered)
    assert _check(tampered)[0] is FrameSignatureResult.BAD_SIGNATURE


def test_the_signature_survives_a_json_round_trip_with_reordered_keys() -> None:
    signed = _signed()
    received = json.loads(json.dumps(dict(reversed(list(signed.items())))))
    assert _check(received) == (FrameSignatureResult.VALID, 1)


@pytest.mark.parametrize(
    "envelope",
    [
        "text",
        {},
        {"version": 2},
    ],
)
def test_a_malformed_envelope_is_named(envelope: Any) -> None:
    frame = dict(FRAME)
    frame[FRAME_SIGNATURE_FIELD] = envelope
    assert _check(frame)[0] is FrameSignatureResult.MALFORMED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("algorithm", "rsa"),
        ("key_id", ""),
        ("hub_id", 5),
        ("session_nonce", None),
        ("value", ""),
        ("seq", 0),
        ("seq", True),
        ("seq", "1"),
    ],
)
def test_each_envelope_field_is_checked(field: str, value: Any) -> None:
    signed = _signed()
    signed[FRAME_SIGNATURE_FIELD][field] = value
    assert _check(signed)[0] is FrameSignatureResult.MALFORMED


def test_an_unsigned_frame_or_an_event_signature_is_missing_a_frame_signature() -> None:
    assert _check(dict(FRAME))[0] is FrameSignatureResult.MISSING
    event_signed = sign_event_frame(FRAME, key_id="k", private_key=KEY, nonce="n", sequence=1)
    assert _check(event_signed)[0] is FrameSignatureResult.MISSING


def test_a_frame_rfc_8785_cannot_represent_is_malformed_or_refused() -> None:
    signed = _signed()
    signed["ttl"] = float("nan")
    assert _check(signed)[0] is FrameSignatureResult.MALFORMED
    with pytest.raises(FrameCanonicalError):
        sign_session_frame(
            {**FRAME, "big": 2**60},
            key_id="k",
            private_key=KEY,
            hub_id="hub-1",
            session_nonce="n-1",
            seq=1,
        )
