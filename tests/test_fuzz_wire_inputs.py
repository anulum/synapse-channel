# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — property-based fuzzing for bounded wire decoding
"""Property-based fuzz targets for the production wire JSON decoder."""

from __future__ import annotations

import json
import os

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from synapse_channel.core.protocol import MAX_JSON_DEPTH, loads_bounded

_FUZZ_EXAMPLES = int(os.environ.get("SYNAPSE_FUZZ_EXAMPLES", "100"))
if not 1 <= _FUZZ_EXAMPLES <= 10_000:
    raise RuntimeError("SYNAPSE_FUZZ_EXAMPLES must be between 1 and 10000")

_SCALARS: SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=128),
)
_JSON_VALUES: SearchStrategy[object] = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=8),
        st.dictionaries(st.text(max_size=32), children, max_size=8),
    ),
    max_leaves=32,
)


@given(raw=st.binary(max_size=16_384))
@example(raw=b"")
@example(raw=b"\xff\xfe\xfa")
@example(raw=b'{"sender":"agent","type":"chat","payload":"ok"}')
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_wire_decoder_accepts_or_deliberately_rejects_arbitrary_bytes(raw: bytes) -> None:
    """Arbitrary bytes either decode or fail with one documented reject class."""
    try:
        loads_bounded(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return


@given(value=_JSON_VALUES)
@example(value={"sender": "agent", "type": "chat", "payload": "recorded-frame"})
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_wire_decoder_round_trips_json_values(value: object) -> None:
    """Every generated JSON value survives the production decoder unchanged."""
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")

    assert loads_bounded(encoded) == value


@given(
    depth=st.integers(min_value=0, max_value=MAX_JSON_DEPTH + 128),
    container=st.sampled_from(("array", "object")),
)
@example(depth=MAX_JSON_DEPTH, container="array")
@example(depth=MAX_JSON_DEPTH + 1, container="object")
@settings(max_examples=_FUZZ_EXAMPLES, deadline=None, print_blob=True)
def test_wire_decoder_enforces_depth_before_json_recursion(depth: int, container: str) -> None:
    """Generated nesting at the limit decodes and deeper nesting fails normally."""
    if container == "array":
        encoded = "[" * depth + "0" + "]" * depth
    else:
        encoded = '{"x":' * depth + "0" + "}" * depth

    if depth > MAX_JSON_DEPTH:
        with pytest.raises(json.JSONDecodeError, match="nested deeper than"):
            loads_bounded(encoded)
    else:
        loads_bounded(encoded)
