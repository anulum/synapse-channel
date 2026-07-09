# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio snapshot: project the dashboard payload into the command-centre shape
"""Project the read-only dashboard payload into the Studio command-centre shape.

The Studio command centre reads one JSON contract: a single verdict, a row of headline
counters, and the structured agents / claims / tasks / conflicts / risk behind them. This
module is the projection that produces it — a pure dict-to-dict reshape of the existing
``DashboardSnapshot.to_dict()`` payload (fleet + risk views), so Studio adds no new hub
call and no new state, only a curated view of what the dashboard already exposes.

The projection foregrounds the risk **verdict** (the reserved red/amber/green signal),
derives every headline count from the same lists the panels render, and adds a
security-posture section from the same source payload. It is robust to a partial payload:
a missing section projects to its empty default rather than raising, so a degraded hub
still yields a renderable snapshot. :func:`frozen_studio_snapshot` returns a deterministic
sample for offline rendering and tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.dashboard_security_posture import build_security_posture

STUDIO_SNAPSHOT_PATH = "/studio.json"
"""The HTTP path the live Studio snapshot is served at."""

UNKNOWN_VERDICT = "unknown"
"""The verdict reported when the payload carries no risk level."""


def _mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return list(value) if isinstance(value, list) else []


def _int(value: object) -> int:
    """Return ``value`` when it is a non-boolean integer, else ``0``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def build_studio_snapshot(dashboard: Mapping[str, Any]) -> dict[str, Any]:
    """Project a ``DashboardSnapshot.to_dict()`` payload into the Studio command-centre shape.

    Parameters
    ----------
    dashboard : Mapping[str, Any]
        The dashboard payload (``roster``/``state``/``board``/``manifest`` plus the derived
        ``fleet`` and ``risk`` views).

    Returns
    -------
    dict[str, Any]
        ``verdict``, ``generated_at``, a ``headline`` counter row, and the ``agents``,
        ``claims``, ``tasks``, ``conflicts``, ``security_posture``, and ``risk``
        sections behind it. Counts are derived from the same lists the sections carry, so
        they cannot drift apart.
    """
    fleet = _mapping(dashboard.get("fleet"))
    risk = _mapping(dashboard.get("risk"))
    agents = _mapping(fleet.get("agents"))
    claims = _mapping(fleet.get("claims"))
    tasks = _mapping(fleet.get("tasks"))

    live = _list(agents.get("live"))
    waiters = _list(agents.get("waiters"))
    missing_waiters = _list(agents.get("missing_waiters"))
    active_claims = _list(claims.get("active_claims"))
    stale_claims = _list(claims.get("stale_claims"))
    ready = _list(tasks.get("ready"))
    blocked = _list(tasks.get("blocked"))
    conflicts = _list(fleet.get("branch_conflicts"))
    signals = _list(risk.get("signals"))
    verdict = str(risk.get("level", UNKNOWN_VERDICT))

    return {
        "verdict": verdict,
        "generated_at": fleet.get("generated_at"),
        "headline": {
            "agents_live": len(live),
            "waiters_missing": len(missing_waiters),
            "claims_active": _int(claims.get("active")),
            "claims_stale": _int(claims.get("stale")),
            "tasks_ready": len(ready),
            "tasks_blocked": len(blocked),
            "branch_conflicts": len(conflicts),
            "risk_signals": len(signals),
        },
        "agents": {"live": live, "waiters": waiters, "missing_waiters": missing_waiters},
        "claims": {"active": active_claims, "stale": stale_claims},
        "tasks": {"ready": ready, "blocked": blocked, "graph": fleet.get("task_graph")},
        "conflicts": conflicts,
        "security_posture": build_security_posture(dashboard),
        "risk": {
            "level": verdict,
            "signals": signals,
            "safe_next_work": _list(risk.get("safe_next_work")),
        },
    }


def frozen_studio_snapshot() -> dict[str, Any]:
    """Return a deterministic, representative Studio snapshot for offline rendering and tests.

    Built by running :func:`build_studio_snapshot` over a fixed dashboard payload, so the
    sample exercises the real projection rather than hand-mirroring its shape.
    """
    return build_studio_snapshot(
        {
            "fleet": {
                "agents": {
                    "live": ["SCPN-FUSION-CORE/claude-a1", "REMANENTIA/codex-b2"],
                    "waiters": ["SCPN-FUSION-CORE/claude-a1"],
                    "missing_waiters": ["DIRECTOR-AI/claude-c3"],
                },
                "claims": {
                    "active": 2,
                    "stale": 1,
                    "active_claims": [
                        {"owner": "SCPN-FUSION-CORE/claude-a1", "scope": "src/", "lease_ms": 42000},
                        {"owner": "REMANENTIA/codex-b2", "scope": "docs/", "lease_ms": 18000},
                    ],
                    "stale_claims": [{"owner": "DIRECTOR-AI/claude-c3", "scope": "tests/"}],
                },
                "tasks": {
                    "ready": ["build-wheel", "run-suite"],
                    "blocked": [{"task_id": "publish", "depends_on": ["run-suite"]}],
                },
                "task_graph": {"nodes": 3, "edges": 1},
                "receipts": [
                    {
                        "task_id": "build-wheel",
                        "author": "SCPN-FUSION-CORE/claude-a1",
                        "text": "release receipt: evidence=pytest tests/test_studio_snapshot.py -q",
                    }
                ],
                "branch_conflicts": [],
                "generated_at": 1782760000.0,
            },
            "risk": {
                "level": "amber",
                "signals": [
                    {
                        "level": "amber",
                        "category": "waiter",
                        "subject": "DIRECTOR-AI/claude-c3",
                        "detail": "declared but no live waiter",
                    }
                ],
                "safe_next_work": ["build-wheel", "run-suite"],
            },
            "manifest": [
                {
                    "agent": "SCPN-FUSION-CORE/claude-a1",
                    "skills": ["sandbox"],
                    "task_classes": ["wasm"],
                }
            ],
            "agent_roles": {"SCPN-FUSION-CORE/claude-a1": ["SYNAPSE-CHANNEL/operator"]},
            "config_epoch": "sha256:demo",
            "observed_peers": [{"hub_id": "ml350", "reachable": True}],
        }
    )
