# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write JSON boundary regressions
from __future__ import annotations

import json
import sys
from typing import cast

import pytest

from synapse_channel.core.atomic_operations import canonical_request_digest
from synapse_channel.core.protected_write_json import (
    ProtectedWriteJsonError,
    ProtectedWriteJsonLimits,
    decode_protected_write_json,
)

LIMITS = ProtectedWriteJsonLimits(16384, 32, 1024, 1024)


def test_status_wire_preserves_core_digest_and_numeric_types() -> None:
    request = {
        "authority_continuity": "continuity-1",
        "authority_id": "authority-1",
        "body": {"reservation_id": None},
        "enrollment_revision": "enrollment-1",
        "payload": "",
        "proposal_sha256": "0" * 64,
        "request_id": "request-1",
        "schema_version": "synapse-protected-write.v1",
        "sender": "EXAMPLE/author",
        "session_id": "session-1",
        "target": "EXAMPLE/authority",
        "transaction_id": "transaction-1",
        "type": "protected_write_status",
        "timestamp": 1.0,
    }
    parsed = decode_protected_write_json(json.dumps(request).encode(), limits=LIMITS)
    assert parsed == request
    assert type(parsed["timestamp"]) is float
    assert canonical_request_digest(parsed) == (
        "da0b0869339e20f69e1b098a292cdd8393fd4d00f1609dc86657d7dbb10e6828"
    )
    parsed["timestamp"] = 2.0
    assert canonical_request_digest(parsed) == canonical_request_digest(request)
    parsed["request_id"] = "request-2"
    assert canonical_request_digest(parsed) == (
        "0d05b095a9e478a71cd4147dedbb0df7ff652cd017bf1fd6ae4e4d3d7c94a0dc"
    )


@pytest.mark.parametrize("token", ["0", "9007199254740991", "0.0", "1.25", "1e-07"])
def test_exact_numeric_tokens_survive_without_coercion(token: str) -> None:
    value = decode_protected_write_json('{"number":' + token + "}", limits=LIMITS)["number"]
    assert json.dumps(value) == token


@pytest.mark.parametrize(
    "token",
    [
        "-0",
        "-1",
        "9007199254740992",
        "9" * 5000,
        "-0.0",
        "-1.0",
        "1.00",
        "1e0",
        "1E-07",
        "1e-7",
        "1e999",
        "1e-999",
        "9007199254740992.0",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_noncanonical_or_out_of_range_numbers_refuse(token: str) -> None:
    with pytest.raises(ProtectedWriteJsonError):
        decode_protected_write_json('{"number":' + token + "}", limits=LIMITS)


@pytest.mark.parametrize(
    "raw",
    [
        '{"timestamp":1.0,"timestamp":2.0}',
        '{"auth":{"nonce":"one","nonce":"two"}}',
        '{"body":{"x":1,"\\u0078":2}}',
        '{"signature":{"data":[{"x":1,"x":2}]}}',
    ],
)
def test_raw_duplicate_keys_refuse_at_every_depth(raw: str) -> None:
    with pytest.raises(ProtectedWriteJsonError, match="duplicate"):
        decode_protected_write_json(raw, limits=LIMITS)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        '{"x":"\ud800"}',
        '{"x":"\\udfff"}',
        '{"\\ud800":0}',
        b"\xff\xfe{\x00}\x00",
        b"\xef\xbb\xbf{}",
        "\ufeff{}",
        "{} trailing",
        '{"x":',
        "[]",
        "null",
        "true",
        "1",
    ],
)
def test_invalid_encoding_structure_and_surrogates_refuse(raw: str | bytes) -> None:
    with pytest.raises(ProtectedWriteJsonError):
        decode_protected_write_json(raw, limits=LIMITS)


def test_raw_string_utf8_size_is_not_character_count() -> None:
    raw = '{"x":"é"}'
    exact = len(raw.encode())
    assert decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(exact, 1, 2, 3))
    for payload in (raw, raw.encode()):
        with pytest.raises(ProtectedWriteJsonError, match="wire"):
            decode_protected_write_json(
                payload, limits=ProtectedWriteJsonLimits(exact - 1, 1, 2, 3)
            )
    with pytest.raises(ProtectedWriteJsonError, match="wire"):
        decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(2, 1, 2, 3))


def test_strings_count_decoded_utf8_bytes_including_keys() -> None:
    for raw in ('{"x":"é"}', '{"é":0}'):
        with pytest.raises(ProtectedWriteJsonError, match="string"):
            decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(100, 1, 1, 3))
    assert decode_protected_write_json('{"x":"\\ud83d\\ude00"}', limits=LIMITS) == {"x": "😀"}


def test_depth_ignores_brackets_and_escaped_quotes_in_strings() -> None:
    request = {"x": '[{\\"}]', "array": [True, False, None, {"n": 0}]}
    raw = json.dumps(request)
    assert (
        decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(1000, 3, 100, 20))
        == request
    )
    with pytest.raises(ProtectedWriteJsonError, match="nesting"):
        decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(1000, 2, 100, 20))


def test_depth_checked_before_python_decoder_recursion() -> None:
    depth = max(10000, sys.getrecursionlimit() * 2)
    raw = '{"x":' + "[" * depth + "0" + "]" * depth + "}"
    with pytest.raises(ProtectedWriteJsonError, match="nesting"):
        decode_protected_write_json(
            raw, limits=ProtectedWriteJsonLimits(len(raw), 32, 10, depth + 5)
        )
    with pytest.raises(ProtectedWriteJsonError, match="excessively nested"):
        decode_protected_write_json(
            raw, limits=ProtectedWriteJsonLimits(len(raw), depth + 5, 10, depth + 5)
        )


def test_node_budget_counts_keys_values_and_containers() -> None:
    raw = '{"x":[null,true]}'
    assert decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(100, 2, 10, 5))
    with pytest.raises(ProtectedWriteJsonError, match="node"):
        decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(100, 2, 10, 4))


@pytest.mark.parametrize("value", [0, -1, True, 1.0])
@pytest.mark.parametrize("field", range(4))
def test_enrollment_limits_require_positive_exact_integers(value: object, field: int) -> None:
    values = [100, 10, 10, 100]
    values[field] = cast(int, value)
    with pytest.raises(ProtectedWriteJsonError, match="limits"):
        ProtectedWriteJsonLimits(*values)


def test_already_decoded_input_is_never_accepted() -> None:
    with pytest.raises(ProtectedWriteJsonError, match="raw"):
        decode_protected_write_json(cast(str, {"x": 1}), limits=LIMITS)
