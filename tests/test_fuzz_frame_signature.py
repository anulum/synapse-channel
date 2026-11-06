# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — property-based fuzzing of frame-signature order and canonical bytes
"""Fuzz the order, replay and canonical-bytes invariants of signed frames."""

from __future__ import annotations

import json
import math
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from synapse_channel.core.aef_canonical import IJSON_MAX_INTEGER
from synapse_channel.core.frame_canonical import FrameCanonicalError, frame_canonical_json
from synapse_channel.core.frame_signature import (
    FrameSignatureResult,
    sign_session_frame,
    verify_session_frame,
)

_FUZZ_EXAMPLES = int(os.environ.get("SYNAPSE_FUZZ_EXAMPLES", "100"))
if not 1 <= _FUZZ_EXAMPLES <= 10_000:
    raise RuntimeError("SYNAPSE_FUZZ_EXAMPLES must be between 1 and 10000")

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))

_SCALARS: SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(),
    st.text(max_size=64),
)
_VALUES: SearchStrategy[object] = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=6),
        st.dictionaries(st.text(max_size=16), children, max_size=6),
    ),
    max_leaves=24,
)


def _representable(value: object) -> bool:
    """Whether every number in ``value`` is one a signed frame may hold."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, int):
        return -IJSON_MAX_INTEGER <= value <= IJSON_MAX_INTEGER
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_representable(item) for item in value)
    assert isinstance(value, dict)
    return all(_representable(item) for item in value.values())


@given(value=_VALUES)
@example(value={"n": 2**53})
@example(value={"x": float("nan")})
@example(value={"s": "\ud800"})
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_canonical_bytes_parse_back_or_refuse_exactly_what_a_frame_cannot_hold(
    value: object,
) -> None:
    """What a sender canonicalizes, the hub canonicalizes to the same bytes.

    The hub sees the frame as JSON text parsed back (``json.loads`` of
    ``json.dumps``), so its canonical bytes must equal the sender's; a value a
    frame cannot hold is refused with the one documented error.
    """
    try:
        rendered = frame_canonical_json(value)
    except FrameCanonicalError:
        text = json.dumps(value)
        assert not _representable(value) or "\\ud8" in text or "\\udc" in text
        return
    assert _representable(value)
    assert frame_canonical_json(json.loads(json.dumps(value))) == rendered


@given(seqs=st.lists(st.integers(min_value=-2, max_value=40), min_size=1, max_size=40))
@example(seqs=[1, 1])
@example(seqs=[3, 2, 4])
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_only_a_strictly_growing_sequence_is_admitted(seqs: list[int]) -> None:
    """Whatever order frames arrive in, exactly the new highest seqs are admitted."""
    last = 0
    for seq in seqs:
        frame = {"sender": "P/a", "type": "claim", "target": "System", "task_id": "t"}
        signed = sign_session_frame(
            frame, key_id="k", private_key=_KEY, hub_id="h", session_nonce="n", seq=seq
        )
        result, after = verify_session_frame(
            signed,
            public_key=_KEY.public_key(),
            key_id="k",
            hub_id="h",
            session_nonce="n",
            last_seq=last,
        )
        if seq < 1:
            assert (result, after) == (FrameSignatureResult.MALFORMED, last)
        elif seq > last:
            assert (result, after) == (FrameSignatureResult.VALID, seq)
            last = seq
        else:
            assert (result, after) == (FrameSignatureResult.REPLAYED, last)
