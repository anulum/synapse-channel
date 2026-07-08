# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — receiver wake-capability vocabulary
"""Receiver wake-capability vocabulary shared by clients, hub, and renderers."""

from __future__ import annotations

WAKE_DIRECT = "direct"
"""A real agent client receives frames directly and is expected to act on them."""

WAKE_PASSIVE = "passive"
"""A passive socket can receive a frame but does not prove an agent pane was woken."""

WAKE_PANE_BRIDGE = "pane_bridge"
"""A receiver bridge injects a wake prompt into a provider pane."""

WAKE_UNKNOWN = "unknown"
"""No receiver capability has been declared."""

_KNOWN_CAPABILITIES = frozenset({WAKE_DIRECT, WAKE_PASSIVE, WAKE_PANE_BRIDGE, WAKE_UNKNOWN})


def normalize_wake_capability(value: object, *, default: str = WAKE_UNKNOWN) -> str:
    """Return a known wake-capability token.

    Parameters
    ----------
    value : object
        Raw capability declared by a client heartbeat.
    default : str, optional
        Capability returned when ``value`` is absent, blank, or unknown. Unknown
        defaults themselves fall back to :data:`WAKE_UNKNOWN`.

    Returns
    -------
    str
        One of the ``WAKE_*`` constants.
    """
    fallback = default if default in _KNOWN_CAPABILITIES else WAKE_UNKNOWN
    if not isinstance(value, str):
        return fallback
    token = value.strip().lower().replace("-", "_")
    return token if token in _KNOWN_CAPABILITIES else fallback


def wake_capability_label(value: object) -> str:
    """Return the operator-facing label for a wake-capability token.

    Parameters
    ----------
    value : object
        Raw or normalized capability token.

    Returns
    -------
    str
        Short label suitable for CLI output and receipt payloads.
    """
    capability = normalize_wake_capability(value)
    if capability == WAKE_DIRECT:
        return "direct agent"
    if capability == WAKE_PASSIVE:
        return "passive receiver"
    if capability == WAKE_PANE_BRIDGE:
        return "pane bridge"
    return "unknown wake capability"
