# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tolerant coercion of untrusted numeric fields to bounded int/float
"""Tolerant coercion of an untrusted numeric field to a bounded ``int``/``float``.

Two numeric-coercion contracts live in the hub and must not be conflated:

* **Tolerant** (this module) — a client-supplied *limit* or *cursor* that should be read
  as leniently as ``int()``/``float()`` do (a numeric string, a truncating float, even a
  ``bool``) and fall back to a caller default when it is unusable. Use
  :func:`safe_int` / :func:`safe_float`.
* **Strict guard field** (``finding_coercion._opt_int`` / ``_opt_float`` and
  ``SynapseHub._optional_int``) — a field that must be a genuine finite number or count as
  *absent*, so a stray ``true`` or ``"5"`` is rejected rather than coerced. Those stay
  separate by design; do not fold them into these helpers.

Both contracts reject the non-finite hazard: ``json.loads`` yields ``inf``/``nan`` from the
``Infinity``/``NaN`` tokens, and a JSON integer too large for a double overflows on
``float``. ``int(inf)`` raises ``OverflowError`` and ``int(nan)`` raises ``ValueError``, so
an unguarded conversion of a hostile numeric field would drop the requester's connection.
"""

from __future__ import annotations

import math
from typing import Any, overload

__all__ = ["safe_float", "safe_int"]


@overload
def safe_int(
    value: Any, *, default: int, min_value: int | None = ..., max_value: int | None = ...
) -> int: ...


@overload
def safe_int(
    value: Any,
    *,
    default: None = ...,
    min_value: int | None = ...,
    max_value: int | None = ...,
) -> int | None: ...


def safe_int(
    value: Any,
    *,
    default: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    """Coerce ``value`` to ``int``, or return ``default`` when it cannot be.

    Reads ``value`` as leniently as ``int()`` (a numeric string, a truncating float, a
    ``bool``). ``TypeError``/``ValueError`` (non-numeric) and ``OverflowError`` (a
    non-finite float such as a JSON ``1e400`` decoded to ``inf``) fall back to ``default``.
    A successfully coerced result is clamped into ``[min_value, max_value]`` when those
    bounds are given; ``default`` is returned unclamped, so pass an in-range default.

    Parameters
    ----------
    value : Any
        The field to coerce.
    default : int or None, optional
        Returned when ``value`` cannot be coerced. Defaults to ``None``.
    min_value, max_value : int or None, optional
        Inclusive clamp bounds applied to a coerced value. ``None`` disables the bound.

    Returns
    -------
    int or None
        The coerced, clamped value, or ``default``.
    """
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if min_value is not None and result < min_value:
        result = min_value
    if max_value is not None and result > max_value:
        result = max_value
    return result


@overload
def safe_float(value: Any, *, default: float, finite: bool = ...) -> float: ...


@overload
def safe_float(value: Any, *, default: None = ..., finite: bool = ...) -> float | None: ...


def safe_float(value: Any, *, default: float | None = None, finite: bool = True) -> float | None:
    """Coerce ``value`` to ``float``, or return ``default`` when it cannot be.

    ``TypeError``/``ValueError``/``OverflowError`` (the last from a JSON integer too large
    for a double) fall back to ``default``. When ``finite`` (the default) a coerced
    ``inf``/``nan`` is rejected to ``default`` too, so a non-finite value never enters an
    ordering or window comparison.

    Parameters
    ----------
    value : Any
        The field to coerce.
    default : float or None, optional
        Returned when ``value`` cannot be coerced (or is non-finite under ``finite``).
    finite : bool, optional
        Reject a coerced ``inf``/``nan`` to ``default``. Defaults to ``True``.

    Returns
    -------
    float or None
        The coerced value, or ``default``.
    """
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if finite and not math.isfinite(result):
        return default
    return result
