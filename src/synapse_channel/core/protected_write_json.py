# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded protected-write JSON decoding
"""Decode the protected-write numeric profile without discarding raw ambiguity.

This boundary validates JSON representation, not protocol fields, authentication,
proposal integrity or permission to mutate. Ordinary transport JSON is unchanged.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import NoReturn


class ProtectedWriteJsonError(ValueError):
    """A protected-write document violates its explicit JSON representation limits."""


@dataclass(frozen=True)
class ProtectedWriteJsonLimits:
    """Explicit enrollment limits for one raw protected-write document.

    Parameters
    ----------
    max_wire_bytes:
        Maximum UTF-8 document length, including whitespace.
    max_depth:
        Maximum nested object/array count; the outer object counts as one.
    max_string_bytes:
        Maximum UTF-8 length of any decoded string, including object keys.
    max_nodes:
        Maximum decoded value count; containers and object keys count as nodes.
    """

    max_wire_bytes: int
    max_depth: int
    max_string_bytes: int
    max_nodes: int

    def __post_init__(self) -> None:
        """Refuse absent, disabled or coerced enrollment budgets."""
        for value in (self.max_wire_bytes, self.max_depth, self.max_string_bytes, self.max_nodes):
            if type(value) is not int or value <= 0:
                raise ProtectedWriteJsonError("JSON limits must be explicit positive integers")


def decode_protected_write_json(
    raw: str | bytes, *, limits: ProtectedWriteJsonLimits
) -> dict[str, object]:
    """Decode one bounded UTF-8 object before semantic validation or hashing.

    Parameters
    ----------
    raw:
        Raw text or UTF-8 bytes, not an already decoded transport dictionary.
    limits:
        Explicit positive limits from the configured enrollment.

    Returns
    -------
    dict[str, object]
        Fresh JSON object preserving integer versus binary64 number types.

    Raises
    ------
    ProtectedWriteJsonError
        For invalid encoding, malformed JSON, duplicate keys, unpaired surrogates,
        exceeded limits, negative/out-of-range numbers or noncanonical binary64
        tokens. This representation check does not authenticate the document.
    """
    if isinstance(raw, bytes):
        if len(raw) > limits.max_wire_bytes:
            raise ProtectedWriteJsonError("JSON wire budget exceeded")
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise ProtectedWriteJsonError("JSON must be UTF-8 without surrogates") from exc
    elif isinstance(raw, str):
        if len(raw) > limits.max_wire_bytes:
            raise ProtectedWriteJsonError("JSON wire budget exceeded")
        try:
            length = len(raw.encode("utf-8"))
        except UnicodeError as exc:
            raise ProtectedWriteJsonError("JSON must be UTF-8 without surrogates") from exc
        if length > limits.max_wire_bytes:
            raise ProtectedWriteJsonError("JSON wire budget exceeded")
        text = raw
    else:
        raise ProtectedWriteJsonError("JSON input must be raw text or bytes")
    _check_depth(text, limits.max_depth)
    try:
        value: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=_integer,
            parse_float=_binary64,
            parse_constant=_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProtectedWriteJsonError("malformed or excessively nested JSON") from exc
    if not isinstance(value, dict):
        raise ProtectedWriteJsonError("JSON document must be an object")
    _check_values(value, limits)
    return dict(value)


def _check_depth(text: str, maximum: int) -> None:
    depth = 0
    quoted = escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > maximum:
                raise ProtectedWriteJsonError("JSON nesting budget exceeded")
        elif character in "]}":
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtectedWriteJsonError("duplicate JSON object key")
        result[key] = value
    return result


def _integer(token: str) -> int:
    if token.startswith("-") or len(token) > 16:
        raise ProtectedWriteJsonError("JSON integer outside protected-write range")
    value = int(token)
    if value > (1 << 53) - 1:
        raise ProtectedWriteJsonError("JSON integer outside protected-write range")
    return value


def _binary64(token: str) -> float:
    value = float(token)
    if not math.isfinite(value) or math.copysign(1.0, value) < 0 or value >= 1 << 53:
        raise ProtectedWriteJsonError("JSON binary64 outside protected-write range")
    if json.dumps(value, allow_nan=False) != token:
        raise ProtectedWriteJsonError("JSON binary64 token is not canonical")
    return value


def _constant(token: str) -> NoReturn:
    raise ProtectedWriteJsonError("nonfinite JSON constant is forbidden")


def _check_values(root: dict[str, object], limits: ProtectedWriteJsonLimits) -> None:
    pending: list[object] = [root]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if count > limits.max_nodes:
            raise ProtectedWriteJsonError("JSON node budget exceeded")
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            try:
                length = len(value.encode("utf-8"))
            except UnicodeError as exc:
                raise ProtectedWriteJsonError("unpaired JSON string surrogate") from exc
            if length > limits.max_string_bytes:
                raise ProtectedWriteJsonError("JSON string budget exceeded")
