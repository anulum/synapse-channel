# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — age-mark replayed directed messages at the wake surface
"""Age-mark replayed directed messages at the wake surface.

The durable mailbox replays directed messages that arrived while a waiter was
disconnected. Replay is the right default for a reconnect gap measured in
seconds — but the same mechanism resurfaces a directive sent months ago (a
lost cursor, a re-minted identity, a recycled pid behind an auto-minted name)
in exactly the shape of a live wake, and the receiving session, holding none
of the original context, cannot tell the difference. That is the 2026-07-16
stale-mailbox incident: an era-old version directive replayed as live nearly
drove a dependency downgrade, stopped only by manual verify-at-source.

This module makes "era-old" detectable in-protocol at the moment of reading:
a surfaced message older than :data:`STALE_AFTER_SECONDS` is prefixed with an
unambiguous replay marker carrying its age, so a stale directive can never
present itself in the same shape as a live one. The hub keeps a numeric
client send-time verbatim on the chat envelope, so the age is the sender's
clock against the reader's; a missing or unusable timestamp yields no marker
(fail-open to the old presentation — an unknown age is not evidence of
staleness). The richer delivery-receipt/state model and formal directive TTL
semantics are the v1.0 follow-up; this marker is the v0.99.9 minimal safety
guarantee.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

STALE_AFTER_SECONDS = 900.0
"""Age beyond which a surfaced message is marked as a replay.

Fifteen minutes comfortably covers a reconnect or re-arm gap while still
flagging anything that could plausibly have been concluded, superseded, or
expired by the time it is read.
"""


def message_age_seconds(frame: Mapping[str, Any], *, now: float | None = None) -> float | None:
    """Return the age of a chat frame in seconds, or ``None`` when unknowable.

    Parameters
    ----------
    frame : Mapping[str, Any]
        The chat envelope; its advisory ``timestamp`` is the sender's epoch
        send-time, preserved verbatim by the hub when numeric.
    now : float or None, optional
        The reader's clock, injectable for testing; ``time.time()`` when
        ``None``.

    Returns
    -------
    float or None
        Seconds since the message was sent (clamped non-negative, so clock
        skew never yields a negative age), or ``None`` for a missing, boolean,
        non-numeric, non-finite, or non-positive timestamp.
    """
    raw = frame.get("timestamp")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        return None
    current = time.time() if now is None else now
    return max(0.0, current - value)


def format_age(seconds: float) -> str:
    """Return a coarse human age — ``45s``, ``12m``, ``5h``, or ``33d``."""
    value = max(0.0, seconds)
    if value < 60:
        return f"{int(value)}s"
    if value < 3600:
        return f"{int(value // 60)}m"
    if value < 86400:
        return f"{int(value // 3600)}h"
    return f"{int(value // 86400)}d"


def stale_marker(age_seconds: float | None, *, stale_after: float = STALE_AFTER_SECONDS) -> str:
    """Return the wake-line prefix for a message of this age.

    Parameters
    ----------
    age_seconds : float or None
        The message age from :func:`message_age_seconds`; ``None`` (unknown)
        is treated as fresh, because an unknown age is not evidence of
        staleness.
    stale_after : float, optional
        The freshness horizon in seconds.

    Returns
    -------
    str
        The empty string for a fresh message, or an unambiguous
        ``"[replayed <age> ago] "`` prefix for a stale one.
    """
    if age_seconds is None or age_seconds < stale_after:
        return ""
    return f"[replayed {format_age(age_seconds)} ago] "


__all__ = [
    "STALE_AFTER_SECONDS",
    "format_age",
    "message_age_seconds",
    "stale_marker",
]
