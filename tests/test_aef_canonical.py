# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format canonical JSON regressions

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synapse_channel.core.aef_canonical import (
    IJSON_MAX_INTEGER,
    IJSON_MIN_INTEGER,
    AefCanonicalizationError,
    canonical_json,
    canonicalize_json,
)

_SIGNED_DOCUMENT = {
    "action": "send",
    "actor": {"agent_id": "a"},
    "aef": "0.1",
    "hub_id": "h1.example.com",
    "issued_at": 1783940400000,
    "log_id": "f3320c94a8b070b04d652b5b0099baa9e12ff8cf8f375093282c7c31becbe0d6",
    "prev_receipt": "aef1:" + "0" * 64,
    "receipt_id": "aef1:cb519c19f7ce43774a2aeeef71f02a9021a546962570cceb51b950fd57eeea5c",
    "receipt_type": "message",
    "seq": 7,
    "signature": {
        "alg": "ed25519",
        "domain": "aef:receipt:v0.1",
        "key_id": "56475aa75463474c",
    },
    "subject": {
        "body_sha256": "f" * 64,
        "message_id": 3,
        "sender": "a",
        "target": "b",
    },
}

_SIGNED_BYTES = (
    b'{"action":"send","actor":{"agent_id":"a"},"aef":"0.1",'
    b'"hub_id":"h1.example.com","issued_at":1783940400000,'
    b'"log_id":"f3320c94a8b070b04d652b5b0099baa9e12ff8cf8f375093282c7c31becbe0d6",'
    b'"prev_receipt":"aef1:0000000000000000000000000000000000000000000000000000000000000000",'
    b'"receipt_id":"aef1:cb519c19f7ce43774a2aeeef71f02a9021a546962570cceb51b950fd57eeea5c",'
    b'"receipt_type":"message","seq":7,'
    b'"signature":{"alg":"ed25519","domain":"aef:receipt:v0.1",'
    b'"key_id":"56475aa75463474c"},'
    b'"subject":{"body_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",'
    b'"message_id":3,"sender":"a","target":"b"}}'
)

_SURROGATE_CATEGORY: tuple[Literal["Cs"], ...] = ("Cs",)
_SCALAR_TEXT = st.text(alphabet=st.characters(exclude_categories=_SURROGATE_CATEGORY))
_JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=IJSON_MIN_INTEGER, max_value=IJSON_MAX_INTEGER)
    | _SCALAR_TEXT,
    lambda children: (
        st.lists(children, max_size=8) | st.dictionaries(_SCALAR_TEXT, children, max_size=8)
    ),
    max_leaves=40,
)

_VECTOR_PATH = Path(__file__).parent / "fixtures" / "aef_canonical_v0_1.json"


def test_worked_aef_vector_reproduces_exact_canonical_bytes() -> None:
    rendered = canonical_json(_SIGNED_DOCUMENT)

    assert rendered == _SIGNED_BYTES
    assert len(rendered) == 607
    assert rendered[:64].hex() == (
        "7b22616374696f6e223a2273656e64222c226163746f72223a7b226167656e745f6964223a"
        "2261227d2c22616566223a22302e31222c226875625f6964223a22"
    )


def test_language_neutral_conformance_vectors_reproduce_exact_hex() -> None:
    document = json.loads(_VECTOR_PATH.read_text(encoding="utf-8"))

    assert document["format"] == "aef-canonical-v0.1"
    assert len(document["vectors"]) == 4
    for vector in document["vectors"]:
        assert canonicalize_json(vector["input_json"]).hex() == vector["canonical_hex"]


def test_parser_normalizes_member_order_and_insignificant_whitespace() -> None:
    assert canonicalize_json(' { "z" : [ true, null ], "a" : 2 } ') == (b'{"a":2,"z":[true,null]}')


def test_object_keys_use_utf16_code_unit_order() -> None:
    non_bmp = "\U00010000"
    private_use = "\ue000"

    assert canonical_json({private_use: 2, non_bmp: 1}) == ('{"\U00010000":1,"\ue000":2}'.encode())


def test_strings_use_minimal_escapes_and_raw_utf8() -> None:
    value = 'quote" slash\\ controls\b\t\n\f\r\x00\x1f café \u2028'

    expected = '"quote\\" slash\\\\ controls\\b\\t\\n\\f\\r\\u0000\\u001f café \u2028"'

    assert canonical_json(value) == expected.encode()


def test_json_scalar_and_integer_boundaries_are_stable() -> None:
    assert canonical_json([None, True, False, IJSON_MIN_INTEGER, 0, IJSON_MAX_INTEGER]) == (
        b"[null,true,false,-9007199254740991,0,9007199254740991]"
    )


@pytest.mark.parametrize(
    "value",
    [
        IJSON_MIN_INTEGER - 1,
        IJSON_MAX_INTEGER + 1,
        1.0,
        b"bytes",
        ("tuple",),
        {1: "non-string-key"},
        {"nested": {"bad": 0.5}},
    ],
)
def test_in_memory_boundary_rejects_values_outside_the_aef_profile(value: object) -> None:
    with pytest.raises(AefCanonicalizationError):
        canonical_json(value)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ('{"a":1,"a":2}', "duplicate"),
        ("-0", "negative zero"),
        ("1.0", "integers only"),
        ("1e2", "integers only"),
        ("NaN", "integers only"),
        ("Infinity", "integers only"),
        (str(IJSON_MAX_INTEGER + 1), "I-JSON exact range"),
        ("1" * 5000, "integer literal is too large"),
        ("[1,]", "invalid AEF JSON"),
    ],
)
def test_parser_rejects_ambiguous_or_non_profile_json(raw: str, match: str) -> None:
    with pytest.raises(AefCanonicalizationError, match=match):
        canonicalize_json(raw)


@pytest.mark.parametrize("raw", [b"\xff", b"\xef\xbb\xbf{}", "\ufeff{}"])
def test_parser_requires_unmarked_strict_utf8(raw: str | bytes) -> None:
    with pytest.raises(AefCanonicalizationError):
        canonicalize_json(raw)


@pytest.mark.parametrize("value", ["\ud800", "\udfff", {"\ud800": 1}])
def test_unpaired_surrogates_are_rejected(value: object) -> None:
    with pytest.raises(AefCanonicalizationError, match="unpaired surrogates"):
        canonical_json(value)


def test_unicode_is_not_normalized_by_the_canonicalizer() -> None:
    composed = canonical_json("\u00e9")
    decomposed = canonical_json("e\u0301")

    assert composed != decomposed
    assert composed == '"é"'.encode()
    assert decomposed == b'"e\xcc\x81"'


def test_parser_and_in_memory_paths_produce_identical_bytes() -> None:
    raw = b'{"subject":{"target":"b","sender":"a"},"aef":"0.1"}'

    assert canonicalize_json(raw) == canonical_json(
        {"aef": "0.1", "subject": {"sender": "a", "target": "b"}}
    )


@given(_JSON_VALUES)
def test_every_generated_profile_value_roundtrips_through_wire_bytes(value: object) -> None:
    rendered = canonical_json(value)

    assert canonicalize_json(rendered) == rendered


def test_parser_rejects_non_text_input() -> None:
    with pytest.raises(AefCanonicalizationError, match="str or bytes"):
        canonicalize_json(cast("str | bytes", bytearray(b"{}")))


def test_cyclic_in_memory_value_is_rejected_at_the_api_boundary() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(AefCanonicalizationError, match="nesting exceeds"):
        canonical_json(cyclic)
