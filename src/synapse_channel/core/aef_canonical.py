# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format canonical JSON
"""Restricted RFC 8785 serialization for Agent Evidence Format documents.

AEF v0.1 deliberately narrows JSON Canonicalization Scheme inputs to strings,
booleans, null, arrays, objects, and integers in the I-JSON exact range.  The
legacy Synapse serializers remain separate because changing their historical
bytes would invalidate existing digests and signatures.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from synapse_channel.core.errors import SynapseError

IJSON_MAX_INTEGER = (1 << 53) - 1
"""Largest exactly representable I-JSON integer accepted by AEF v0.1."""

IJSON_MIN_INTEGER = -IJSON_MAX_INTEGER
"""Smallest exactly representable I-JSON integer accepted by AEF v0.1."""

_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class AefCanonicalizationError(SynapseError, ValueError):
    """Raised when a value cannot be represented by the AEF v0.1 JSON profile."""

    code = "aef_canonicalization"


def canonical_json(value: object) -> bytes:
    """Return canonical AEF v0.1 JSON bytes for an in-memory JSON value.

    Parameters
    ----------
    value:
        A JSON value composed only of dictionaries with string keys, lists,
        strings, booleans, null, and integers in the I-JSON exact range.

    Returns
    -------
    bytes
        UTF-8 RFC 8785 bytes under the integer-only AEF v0.1 profile.

    Raises
    ------
    AefCanonicalizationError
        If the value contains a non-JSON type, a float, an out-of-range
        integer, a non-string object key, or an unpaired UTF-16 surrogate.
    """
    try:
        return _render(value).encode("utf-8")
    except RecursionError as exc:
        raise AefCanonicalizationError("AEF JSON nesting exceeds the runtime limit") from exc


def canonicalize_json(raw: str | bytes) -> bytes:
    """Parse JSON with hostile-input checks and return canonical AEF bytes.

    This boundary rejects duplicate object keys, non-integer numbers,
    non-finite constants, negative zero, invalid UTF-8, a UTF-8 BOM, and every
    in-memory constraint enforced by :func:`canonical_json`.

    Parameters
    ----------
    raw:
        A Unicode JSON document or strict UTF-8 bytes without a BOM.

    Returns
    -------
    bytes
        Canonical AEF v0.1 JSON bytes.

    Raises
    ------
    AefCanonicalizationError
        If parsing or canonicalization fails.
    """
    text = _decode(raw)
    try:
        value = json.loads(
            text,
            parse_int=_parse_integer,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
            object_pairs_hook=_unique_object,
        )
    except AefCanonicalizationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise AefCanonicalizationError(f"invalid AEF JSON: {exc}") from exc
    return canonical_json(value)


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AefCanonicalizationError("AEF JSON must be valid UTF-8") from exc
    else:
        raise AefCanonicalizationError("AEF JSON input must be str or bytes")
    if text.startswith("\ufeff"):
        raise AefCanonicalizationError("AEF JSON must not contain a UTF-8 BOM")
    return text


def _parse_integer(raw: str) -> int:
    if raw == "-0":
        raise AefCanonicalizationError("AEF JSON forbids negative zero")
    try:
        value = int(raw)
    except ValueError as exc:
        raise AefCanonicalizationError("AEF JSON integer literal is too large to parse") from exc
    _require_integer_range(value)
    return value


def _reject_non_integer(raw: str) -> NoReturn:
    raise AefCanonicalizationError(f"AEF JSON accepts integers only, got {raw!r}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AefCanonicalizationError(f"duplicate AEF JSON object key: {key!r}")
        result[key] = value
    return result


def _render(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        _require_integer_range(value)
        return str(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        return _render_object(value)
    raise AefCanonicalizationError(f"unsupported AEF JSON value type: {type(value).__name__}")


def _require_integer_range(value: int) -> None:
    if not IJSON_MIN_INTEGER <= value <= IJSON_MAX_INTEGER:
        raise AefCanonicalizationError(
            f"AEF JSON integer {value} is outside the I-JSON exact range"
        )


def _render_object(value: dict[object, object]) -> str:
    members: list[tuple[str, object]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise AefCanonicalizationError("AEF JSON object keys must be strings")
        _require_unicode_scalar_text(key)
        members.append((key, item))
    members.sort(key=lambda member: _utf16_sort_key(member[0]))
    return "{" + ",".join(f"{_quote(key)}:{_render(item)}" for key, item in members) + "}"


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be")


def _quote(value: str) -> str:
    _require_unicode_scalar_text(value)
    rendered: list[str] = ['"']
    for character in value:
        if character == '"':
            rendered.append('\\"')
        elif character == "\\":
            rendered.append("\\\\")
        elif character in _SHORT_ESCAPES:
            rendered.append(_SHORT_ESCAPES[character])
        elif ord(character) <= 0x1F:
            rendered.append(f"\\u{ord(character):04x}")
        else:
            rendered.append(character)
    rendered.append('"')
    return "".join(rendered)


def _require_unicode_scalar_text(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise AefCanonicalizationError("AEF JSON strings must not contain unpaired surrogates")
