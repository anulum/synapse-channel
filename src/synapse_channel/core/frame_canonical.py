# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — RFC 8785 canonical JSON for signed frames
"""Serialise a frame to RFC 8785 (JCS) bytes that every client can reproduce.

A frame signature covers these bytes, so a client in another language must
produce exactly the same ones. RFC 8785 fixes the details that differ between
JSON libraries: object members sorted by UTF-16 code units, strings escaped
minimally, and numbers written the way ECMAScript writes an IEEE-754 double.

Integers must lie in the I-JSON exact range (±(2**53 − 1)); a larger one would
not survive a JavaScript client unchanged. Numbers must be finite. Strings and
member names must be valid Unicode scalar text.
"""

from __future__ import annotations

import math

from synapse_channel.core.aef_canonical import (
    IJSON_MAX_INTEGER,
    IJSON_MIN_INTEGER,
    AefCanonicalizationError,
    jcs_member_key,
    jcs_string,
)
from synapse_channel.core.errors import SynapseError


class FrameCanonicalError(SynapseError, ValueError):
    """Raised when a frame holds a value RFC 8785 cannot represent for signing."""

    code = "frame_canonical_invalid"


def es_number(value: float) -> str:
    """Return a finite double as ECMAScript ``Number.prototype.toString`` writes it.

    Parameters
    ----------
    value : float
        A finite number.

    Returns
    -------
    str
        The shortest round-trip digits in ECMAScript notation.
    """
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    mantissa, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = (whole + fraction).lstrip("0")
    point = len(whole) + (int(exponent) if exponent else 0)
    point -= len(whole + fraction) - len((whole + fraction).lstrip("0"))
    digits = digits.rstrip("0")
    count = len(digits)
    if count <= point <= 21:
        rendered = digits + "0" * (point - count)
    elif 0 < point <= 21:
        rendered = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        rendered = "0." + "0" * -point + digits
    else:
        power = point - 1
        mark = "+" if power >= 0 else "-"
        head = digits if count == 1 else digits[0] + "." + digits[1:]
        rendered = f"{head}e{mark}{abs(power)}"
    return sign + rendered


def _render(value: object) -> str:
    """Render one JSON value canonically."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not IJSON_MIN_INTEGER <= value <= IJSON_MAX_INTEGER:
            raise FrameCanonicalError(f"integer {value} is outside the I-JSON exact range")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FrameCanonicalError("a signed frame cannot hold NaN or an infinity")
        return es_number(value)
    if isinstance(value, str):
        return jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise FrameCanonicalError("object member names must be strings")
        members = sorted(value.items(), key=lambda member: jcs_member_key(member[0]))
        return "{" + ",".join(f"{jcs_string(key)}:{_render(item)}" for key, item in members) + "}"
    raise FrameCanonicalError(f"a signed frame cannot hold a {type(value).__name__}")


def frame_canonical_json(value: object) -> bytes:
    """Return the RFC 8785 UTF-8 bytes of a JSON value.

    Parameters
    ----------
    value : object
        A value built from dicts with string keys, lists, strings, booleans,
        ``None``, I-JSON integers and finite floats.

    Returns
    -------
    bytes
        The canonical bytes.

    Raises
    ------
    FrameCanonicalError
        If the value holds anything RFC 8785 cannot represent here.
    """
    try:
        return _render(value).encode("utf-8")
    except AefCanonicalizationError as exc:
        raise FrameCanonicalError(str(exc)) from None
    except RecursionError:
        raise FrameCanonicalError("frame nesting exceeds the runtime limit") from None
