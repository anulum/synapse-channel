# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio security-posture projection
"""Derive the Studio security-posture panel from the dashboard snapshot.

The Studio command centre needs one compact answer to "which safety surfaces are
available or evidenced right now?" without implying hidden server state it did not
read. This module folds the dashboard payload into five posture rows:

- sandbox grants,
- ACL and role visibility,
- dashboard exposure guard,
- signed federation / peer observation,
- receipt evidence.

Rows distinguish shipped capability from live deployment evidence. A missing
role binding, peer, or receipt is reported as ``amber`` rather than invented as
configured; a missing config fingerprint is also ``amber`` because old hubs or
custom wrappers may not expose the bind/posture evidence the panel needs. The
result is a read-only projection only: it never authorises an action and never
changes hub policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

GREEN = "green"
"""Posture level for a surface with current evidence or a shipped always-on guard."""

AMBER = "amber"
"""Posture level for a surface that is available but lacks current deployment evidence."""

RED = "red"
"""Posture level for a surface with an explicit unsafe observation."""

UNKNOWN = "unknown"
"""Posture level for malformed input where no stronger claim can be made."""

_LEVEL_RANK = {RED: 0, AMBER: 1, UNKNOWN: 2, GREEN: 3}


@dataclass(frozen=True, slots=True)
class SecurityPostureRow:
    """One row in the Studio security-posture panel.

    Parameters
    ----------
    surface : str
        Short label for the safety surface.
    level : str
        ``green``, ``amber``, ``red``, or ``unknown``.
    state : str
        Machine-stable state summary.
    detail : str
        Operator-facing explanation of what the current snapshot proves.
    evidence : str
        Concrete source the row was derived from.
    """

    surface: str
    level: str
    state: str
    detail: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible mapping."""
        return {
            "surface": self.surface,
            "level": self.level,
            "state": self.state,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class SecurityPosture:
    """The derived security posture for the Studio snapshot.

    Parameters
    ----------
    level : str
        Worst row level, or ``green`` when every row is green.
    rows : tuple[SecurityPostureRow, ...]
        Ordered posture rows for the panel.
    """

    level: str
    rows: tuple[SecurityPostureRow, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible mapping."""
        return {
            "level": self.level,
            "rows": [row.to_dict() for row in self.rows],
            "counts": {
                GREEN: sum(1 for row in self.rows if row.level == GREEN),
                AMBER: sum(1 for row in self.rows if row.level == AMBER),
                RED: sum(1 for row in self.rows if row.level == RED),
                UNKNOWN: sum(1 for row in self.rows if row.level == UNKNOWN),
            },
        }


def _mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` as a mapping, or an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _mappings(value: object) -> list[Mapping[str, Any]]:
    """Return mapping entries from a JSON list value."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _non_empty_roles(value: object) -> int:
    """Count agents with at least one live role binding."""
    roles = _mapping(value)
    count = 0
    for binding in roles.values():
        if isinstance(binding, list) and binding:
            count += 1
    return count


def _manifest_mentions(manifest: list[Mapping[str, Any]], needles: set[str]) -> bool:
    """Return whether a capability card mentions one of ``needles``."""
    for card in manifest:
        values: list[str] = []
        for key in ("skills", "task_classes"):
            raw = card.get(key)
            if isinstance(raw, list):
                values.extend(str(item).lower() for item in raw)
        meta = card.get("meta")
        if isinstance(meta, Mapping):
            values.extend(str(item).lower() for item in meta.values())
        if any(any(needle in value for needle in needles) for value in values):
            return True
    return False


def _observed_peer_state(observed_peers: list[Mapping[str, Any]]) -> tuple[str, int]:
    """Return the federation row state and reachable peer count."""
    if not observed_peers:
        return "available-local-only", 0
    reachable = sum(1 for peer in observed_peers if bool(peer.get("reachable")))
    if reachable == len(observed_peers):
        return "observed", reachable
    if reachable:
        return "partially-observed", reachable
    return "configured-unreachable", 0


def _overall_level(rows: tuple[SecurityPostureRow, ...]) -> str:
    """Return the worst posture row level."""
    return min((row.level for row in rows), key=lambda level: _LEVEL_RANK[level])


def build_security_posture(dashboard: Mapping[str, Any]) -> dict[str, object]:
    """Build the Studio security-posture JSON section.

    Parameters
    ----------
    dashboard : Mapping[str, Any]
        ``DashboardSnapshot.to_dict()`` payload including ``manifest``,
        ``agent_roles``, ``config_epoch``, ``observed_peers``, and the derived
        ``fleet`` section.

    Returns
    -------
    dict[str, object]
        A JSON-compatible posture object with ``level``, ordered ``rows``, and
        per-level ``counts``.
    """
    fleet = _mapping(dashboard.get("fleet"))
    manifest = _mappings(dashboard.get("manifest"))
    observed_peers = _mappings(dashboard.get("observed_peers"))
    receipts = _mappings(fleet.get("receipts"))
    role_count = _non_empty_roles(dashboard.get("agent_roles"))
    sandbox_advertised = _manifest_mentions(manifest, {"sandbox", "wasm"})
    peer_state, reachable_peers = _observed_peer_state(observed_peers)
    has_config_epoch = bool(str(dashboard.get("config_epoch") or ""))

    rows = (
        SecurityPostureRow(
            surface="exposure guard",
            level=GREEN if has_config_epoch else AMBER,
            state="pinned" if has_config_epoch else "unreported",
            detail=(
                "dashboard snapshot carries the hub configuration fingerprint"
                if has_config_epoch
                else (
                    "snapshot lacks a configuration fingerprint, so bind/token posture "
                    "is not visible"
                )
            ),
            evidence="snapshot.config_epoch",
        ),
        SecurityPostureRow(
            surface="ACL and roles",
            level=GREEN if role_count else AMBER,
            state="role-bound" if role_count else "no-live-role-bindings",
            detail=(
                f"{role_count} live agent identity binding(s) report role metadata"
                if role_count
                else (
                    "ACL and role surfaces are shipped, but this snapshot reports no "
                    "live role binding"
                )
            ),
            evidence="snapshot.agent_roles",
        ),
        SecurityPostureRow(
            surface="sandbox grants",
            level=GREEN,
            state="advertised" if sandbox_advertised else "available",
            detail=(
                "a live capability card advertises sandbox or WASM work"
                if sandbox_advertised
                else (
                    "sandbox validate/test/run is shipped; no live card advertises sandbox work now"
                )
            ),
            evidence="synapse sandbox validate/test/run",
        ),
        SecurityPostureRow(
            surface="signed federation",
            level=RED if peer_state == "configured-unreachable" else GREEN,
            state=peer_state,
            detail=(
                f"{reachable_peers} observed federation peer(s) are reachable"
                if reachable_peers
                else (
                    "federation signing and pinning are shipped; "
                    "no reachable observed peer is in this snapshot"
                )
            ),
            evidence="snapshot.observed_peers",
        ),
        SecurityPostureRow(
            surface="receipts",
            level=GREEN if receipts else AMBER,
            state="evidenced" if receipts else "no-current-receipts",
            detail=(
                f"{len(receipts)} release receipt row(s) are visible in the board projection"
                if receipts
                else (
                    "release and universal receipt surfaces are shipped; this board has "
                    "no current receipt row"
                )
            ),
            evidence="fleet.receipts",
        ),
    )
    return SecurityPosture(level=_overall_level(rows), rows=rows).to_dict()
