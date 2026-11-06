# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — RFC 8785 canonical JSON for signed frames
"""Pin the canonical bytes and the shared vectors every signing client must match."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.frame_canonical import (
    FrameCanonicalError,
    es_number,
    frame_canonical_json,
)
from synapse_channel.core.frame_signature import (
    FrameSignatureResult,
    sign_session_frame,
    verify_session_frame,
)

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "frame_signature_vectors.json").read_text("utf-8")
)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-1.5, "-1.5"),
        (0.1, "0.1"),
        (0.002, "0.002"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1e22, "1e+22"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (333333333.3333333, "333333333.3333333"),
        (2.9514790517935283e20, "295147905179352830000"),
        (9007199254740992.0, "9007199254740992"),
    ],
)
def test_numbers_are_written_as_ecmascript_writes_them(value: float, text: str) -> None:
    assert es_number(value) == text


def test_the_shared_canonical_vectors_reproduce() -> None:
    for vector in VECTORS["canonical"]:
        assert (
            frame_canonical_json(json.loads(vector["input"])).decode("utf-8")
            == (vector["canonical"])
        )


def test_the_shared_signature_vector_reproduces_and_verifies() -> None:
    vector = VECTORS["signature"]
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(vector["private_key_seed_hex"]))
    signed = sign_session_frame(
        vector["frame"],
        key_id=vector["key_id"],
        private_key=key,
        hub_id=vector["hub_id"],
        session_nonce=vector["session_nonce"],
        seq=vector["seq"],
    )
    assert signed == vector["signed_frame"]
    received = json.loads(json.dumps(vector["signed_frame"], ensure_ascii=True))
    assert verify_session_frame(
        received,
        public_key=key.public_key(),
        key_id=vector["key_id"],
        hub_id=vector["hub_id"],
        session_nonce=vector["session_nonce"],
        last_seq=0,
    ) == (FrameSignatureResult.VALID, vector["seq"])
    assert len(base64.b64decode(signed["frame_signature"]["value"])) == 64


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (float("nan"), "NaN"),
        (float("inf"), "NaN or an infinity"),
        (2**53, "I-JSON"),
        (-(2**53), "I-JSON"),
        ({1: "x"}, "member names must be strings"),
        ({"\ud800": 1}, "surrogate"),
        (["\udfff"], "surrogate"),
        ((1, 2), "tuple"),
    ],
)
def test_values_rfc_8785_cannot_carry_are_refused(value: Any, message: str) -> None:
    with pytest.raises(FrameCanonicalError, match=message):
        frame_canonical_json(value)


def test_deep_nesting_is_refused_not_crashed() -> None:
    deep: Any = []
    for _ in range(100_000):
        deep = [deep]
    with pytest.raises(FrameCanonicalError, match="nesting"):
        frame_canonical_json(deep)
