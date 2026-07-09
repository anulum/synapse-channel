# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — peer clock-skew measurement helpers
"""Peer clock-skew measurement helpers for multi-hub operator surfaces."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClockSkew:
    """One peer clock-skew observation.

    Attributes
    ----------
    peer_timestamp : float
        Timestamp advertised by the peer.
    observed_at : float
        Local timestamp when that peer timestamp was observed.
    seconds : float
        ``observed_at - peer_timestamp``. Positive means the local clock is
        ahead of the peer by that many seconds.
    """

    peer_timestamp: float
    observed_at: float
    seconds: float


@dataclass(frozen=True, slots=True)
class ClockSkewWarning:
    """A peer whose absolute clock skew exceeds the operator threshold."""

    hub_id: str
    seconds: float
    threshold: float


def finite_timestamp(value: object) -> float | None:
    """Return ``value`` as a finite timestamp, or ``None`` when unusable."""
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def measure_clock_skew(peer_timestamp: object, *, observed_at: float) -> ClockSkew | None:
    """Measure local-minus-peer skew from a peer timestamp.

    ``None`` is returned when the peer timestamp is absent, non-finite, boolean,
    or otherwise not timestamp-like.
    """
    timestamp = finite_timestamp(peer_timestamp)
    if timestamp is None:
        return None
    return ClockSkew(
        peer_timestamp=timestamp,
        observed_at=float(observed_at),
        seconds=float(observed_at) - timestamp,
    )


def format_clock_skew(seconds: float) -> str:
    """Render a skew value with a sign and seconds suffix."""
    return f"{seconds:+.3f}s"


def parse_clock_skew_spec(value: str) -> tuple[str, float]:
    """Parse a ``HUB=SECONDS`` clock-skew CLI value.

    Raises
    ------
    ValueError
        If the hub id is missing or the seconds value is non-finite.
    """
    hub_id, sep, raw_seconds = value.partition("=")
    hub_id = hub_id.strip()
    seconds = finite_timestamp(raw_seconds.strip()) if sep else None
    if not sep or not hub_id or seconds is None:
        msg = f"invalid clock-skew spec {value!r}: expected HUB=SECONDS"
        raise ValueError(msg)
    return hub_id, seconds


def clock_skew_warnings(
    skews: Mapping[str, float],
    *,
    threshold: float,
) -> tuple[ClockSkewWarning, ...]:
    """Return sorted warnings for peers whose absolute skew exceeds ``threshold``."""
    if threshold < 0 or not math.isfinite(threshold):
        msg = "clock-skew threshold must be finite and non-negative"
        raise ValueError(msg)
    return tuple(
        ClockSkewWarning(hub_id=hub_id, seconds=seconds, threshold=threshold)
        for hub_id, seconds in sorted(skews.items())
        if abs(seconds) > threshold
    )
