# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format integer timestamp conversion
"""Integer-millisecond time boundary for Agent Evidence Format documents.

Legacy Synapse evidence stores epoch seconds as binary floats.  AEF v0.1 uses
integer epoch milliseconds so every implementation signs the same number.  The
helpers here define migration without mutating any historical event or receipt.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from decimal import ROUND_FLOOR, Decimal, localcontext

from synapse_channel.core.aef_canonical import IJSON_MAX_INTEGER, IJSON_MIN_INTEGER
from synapse_channel.core.errors import SynapseError

_MILLISECONDS_PER_SECOND = 1000
_DECIMAL_PRECISION = len(str(IJSON_MAX_INTEGER)) + len(str(_MILLISECONDS_PER_SECOND))


class AefTimestampError(SynapseError, ValueError):
    """Raised when a value cannot be projected into an AEF v0.1 timestamp."""

    code = "aef_timestamp"


def validate_epoch_ms(timestamp_ms: object) -> int:
    """Return a canonical AEF epoch-millisecond value.

    Parameters
    ----------
    timestamp_ms:
        Candidate integer Unix epoch milliseconds.

    Returns
    -------
    int
        The validated non-boolean integer in the I-JSON exact range.

    Raises
    ------
    AefTimestampError
        If the value is not a canonical AEF timestamp.
    """
    return _require_epoch_ms(timestamp_ms)


def current_epoch_ms(*, clock_ns: Callable[[], int] | None = None) -> int:
    """Return the local wall clock as integer Unix epoch milliseconds.

    Returns
    -------
    int
        The host wall clock floored to the containing millisecond.

    Raises
    ------
    AefTimestampError
        If the host clock lies outside the I-JSON exact integer range.

    Other Parameters
    ----------------
    clock_ns:
        Nanosecond clock provider used by deterministic callers and tests.
        ``None`` selects :func:`time.time_ns`.

    Notes
    -----
    This value remains a self-asserted hub timestamp.  Independent trusted time
    requires an external anchor; integer representation does not add trust.
    """
    now_ns = time.time_ns() if clock_ns is None else clock_ns()
    return _require_epoch_ms(now_ns // 1_000_000)


def legacy_seconds_to_epoch_ms(seconds: int | float) -> int:
    """Project a legacy epoch-seconds value into AEF integer milliseconds.

    Integer seconds scale exactly.  Float seconds use their shortest decimal
    representation and floor to the containing millisecond.  Decimal
    conversion avoids binary multiplication artifacts such as
    ``0.29 * 1000`` falling below 290.

    Parameters
    ----------
    seconds:
        Legacy Unix epoch seconds represented as an integer or finite float.

    Returns
    -------
    int
        Unix epoch milliseconds in the I-JSON exact integer range.

    Raises
    ------
    AefTimestampError
        If the value is a boolean, non-numeric, non-finite, or projects outside
        the AEF integer range.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        raise AefTimestampError("legacy timestamp seconds must be an integer or float")
    if isinstance(seconds, int):
        return _require_epoch_ms(seconds * _MILLISECONDS_PER_SECOND)
    if isinstance(seconds, float) and not math.isfinite(seconds):
        raise AefTimestampError("legacy timestamp seconds must be finite")
    decimal_seconds = Decimal(str(seconds))
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        milliseconds = decimal_seconds * _MILLISECONDS_PER_SECOND
        projected = int(milliseconds.to_integral_value(rounding=ROUND_FLOOR))
    return _require_epoch_ms(projected)


def epoch_ms_to_legacy_seconds(timestamp_ms: int) -> float:
    """Return a compatibility-only float-seconds projection of an AEF timestamp.

    Parameters
    ----------
    timestamp_ms:
        Canonical AEF Unix epoch milliseconds.

    Returns
    -------
    float
        Legacy-compatible seconds.  This float is not canonical AEF evidence
        and may not preserve every millisecond near the I-JSON range limits.

    Raises
    ------
    AefTimestampError
        If ``timestamp_ms`` is not a non-boolean integer in the I-JSON range.
    """
    value = _require_epoch_ms(timestamp_ms)
    return value / _MILLISECONDS_PER_SECOND


def _require_epoch_ms(timestamp_ms: object) -> int:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise AefTimestampError("AEF timestamp must be an integer number of milliseconds")
    if not IJSON_MIN_INTEGER <= timestamp_ms <= IJSON_MAX_INTEGER:
        raise AefTimestampError("AEF timestamp is outside the I-JSON exact integer range")
    return timestamp_ms
