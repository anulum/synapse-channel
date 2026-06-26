# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tolerant finding field coercion helpers
"""Forward-tolerant coercion helpers for finding records."""

from __future__ import annotations

from typing import Any


def _str(raw: Any) -> str:
    """Return ``raw`` stripped if it is a string, else the empty string."""
    return raw.strip() if isinstance(raw, str) else ""


def _opt_str(raw: Any) -> str | None:
    """Return a non-empty stripped string, or ``None`` for absent/blank/non-string."""
    value = _str(raw)
    return value or None


def _opt_int(raw: Any) -> int | None:
    """Return ``raw`` as an int, or ``None`` for a boolean or non-numeric value."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw)


def _opt_float(raw: Any) -> float | None:
    """Return ``raw`` as a float, or ``None`` for a boolean or non-numeric value."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _str_tuple(raw: Any) -> tuple[str, ...]:
    """Return a tuple of the non-blank strings in ``raw``, or ``()`` when not a list."""
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
