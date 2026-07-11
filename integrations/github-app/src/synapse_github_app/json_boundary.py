# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — strict bounded JSON decoder
"""Decode strict UTF-8 JSON only after a non-recursive depth check."""

from __future__ import annotations

import json
from typing import cast


def _exceeds_depth(text: str, maximum: int) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > maximum:
                return True
        elif char in "]}":
            depth = max(depth - 1, 0)
    return False


def loads_strict_bounded(raw: bytes, *, max_depth: int) -> object:
    """Decode strict UTF-8 RFC 8259 JSON within ``max_depth``.

    Raises
    ------
    UnicodeDecodeError
        If the payload is not strict UTF-8.
    json.JSONDecodeError
        If the payload is malformed, non-finite, or too deeply nested.
    ValueError
        If ``max_depth`` is not positive.
    """
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    text = raw.decode("utf-8")
    if _exceeds_depth(text, max_depth):
        raise json.JSONDecodeError(f"JSON exceeds {max_depth} levels", text, 0)

    def reject_non_finite(constant: str) -> float:
        raise json.JSONDecodeError(f"non-finite number {constant}", text, 0)

    return cast(object, json.loads(text, parse_constant=reject_non_finite))
