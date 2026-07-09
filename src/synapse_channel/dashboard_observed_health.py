# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio observed / multi-hub fleet health projection
"""Project advisory observed-peer rows into the Studio multi-hub health panel.

Observed peers (dashboard ``--observed-peer``) and FLEET mirrors never grant local
authority; they are operator views of remote hubs. This module turns the
``observed_peers`` list from a dashboard snapshot into a compact Studio section:

- overall level (green / amber / red / unknown),
- headline counts (configured, reachable, unreachable, lagging),
- per-peer rows (reachability, lag, clock skew, error, observed agent count).

Empty configuration is an honest amber "not configured" state — not green — so a
local-only hub does not look federated. The projection is pure and never fetches
the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

GREEN = "green"
AMBER = "amber"
RED = "red"
UNKNOWN = "unknown"

_LEVEL_RANK = {RED: 0, AMBER: 1, UNKNOWN: 2, GREEN: 3}


def _mappings(value: object) -> list[Mapping[str, Any]]:
    """Return mapping entries from a JSON list value."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_int(value: object) -> int | None:
    """Return a non-bool integer, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_float(value: object) -> float | None:
    """Return a non-bool float or int as float, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _peer_row(peer: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one observed-peer dict into a Studio row."""
    hub_id = str(peer.get("hub_id") or peer.get("id") or "").strip() or "unknown"
    reachable = bool(peer.get("reachable"))
    lag = _optional_int(peer.get("lag"))
    skew = _optional_float(peer.get("clock_skew_seconds"))
    error = str(peer.get("error") or "").strip()
    agents = peer.get("observed_agents")
    if isinstance(agents, Sequence) and not isinstance(agents, (str, bytes)):
        agent_count = len(agents)
    else:
        agent_count = 0
    if reachable:
        level = AMBER if (lag is not None and lag > 0) else GREEN
        state = "lagging" if level == AMBER else "reachable"
    else:
        level = RED
        state = "unreachable"
    detail_parts: list[str] = []
    if reachable:
        detail_parts.append(f"{agent_count} observed claim owner(s)")
        if lag is not None:
            detail_parts.append(f"lag={lag}")
        if skew is not None:
            detail_parts.append(f"skew={skew:.3f}s")
    elif error:
        detail_parts.append(error)
    else:
        detail_parts.append("peer fetch failed")
    return {
        "hub_id": hub_id,
        "uri": str(peer.get("uri") or ""),
        "reachable": reachable,
        "level": level,
        "state": state,
        "lag": lag,
        "clock_skew_seconds": skew,
        "observed_agents": agent_count,
        "error": error,
        "detail": "; ".join(detail_parts),
    }


def build_observed_fleet_health(dashboard: Mapping[str, Any]) -> dict[str, Any]:
    """Build the Studio multi-hub / observed-peer health section.

    Parameters
    ----------
    dashboard : Mapping[str, Any]
        Dashboard snapshot payload; uses ``observed_peers`` when present.

    Returns
    -------
    dict[str, Any]
        ``level``, ``configured``, headline counts, and ordered ``peers`` rows.
    """
    peers_raw = _mappings(dashboard.get("observed_peers"))
    if not peers_raw:
        return {
            "level": AMBER,
            "configured": False,
            "peers_total": 0,
            "peers_reachable": 0,
            "peers_unreachable": 0,
            "peers_lagging": 0,
            "peers": [],
            "detail": (
                "no observed peers in this snapshot; start the dashboard with "
                "--observed-peer HUB=URI (FLEET mirrors stay advisory)"
            ),
        }
    rows = [_peer_row(peer) for peer in peers_raw]
    reachable = sum(1 for row in rows if row["reachable"])
    unreachable = len(rows) - reachable
    lagging = sum(1 for row in rows if row["state"] == "lagging")
    if unreachable:
        level = RED
    elif lagging:
        level = AMBER
    else:
        level = GREEN
    return {
        "level": level,
        "configured": True,
        "peers_total": len(rows),
        "peers_reachable": reachable,
        "peers_unreachable": unreachable,
        "peers_lagging": lagging,
        "peers": rows,
        "detail": (
            f"{reachable}/{len(rows)} peer(s) reachable"
            + (f", {lagging} lagging" if lagging else "")
            + (f", {unreachable} unreachable" if unreachable else "")
        ),
    }
