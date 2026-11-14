# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the views a dashboard snapshot derives from its whole board
"""Derive the fleet, risk and headline sections of a dashboard snapshot.

All three are computed from the whole board and state, before the served
document is bounded (:mod:`synapse_channel.dashboard_bounds`), so a count never
depends on what a surface was sent.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.dashboard_fleet import build_fleet_visibility
from synapse_channel.dashboard_headline import build_headline
from synapse_channel.dashboard_observed_health import build_observed_fleet_health
from synapse_channel.dashboard_risk import build_risk_view
from synapse_channel.dashboard_risk_guidance import build_risk_guidance
from synapse_channel.dashboard_seats import build_seats

if TYPE_CHECKING:
    from synapse_channel.dashboard import DashboardSnapshot


def derived_views(
    snapshot: DashboardSnapshot,
    observed_peers: Sequence[dict[str, Any]],
    *,
    a2a_state_file: str | Path | None = None,
) -> dict[str, Any]:
    """Return the ``fleet``, ``risk`` and ``headline`` sections for ``snapshot``.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        The whole snapshot fetched from the hub.
    observed_peers : Sequence[dict[str, Any]]
        The snapshot's observed peers, already serialised.
    a2a_state_file : str, pathlib.Path, or None, optional
        Optional persisted A2A bridge state for the fleet's A2A summary.

    Returns
    -------
    dict[str, Any]
        ``{"fleet": …, "risk": …, "headline": …}``; ``fleet["seats"]`` is the
        seat projection of :mod:`synapse_channel.dashboard_seats`.
    """
    fleet = build_fleet_visibility(snapshot, a2a_state_file=a2a_state_file)
    fleet_section = fleet.to_dict()
    hub_time = snapshot.state.get("generated_at")
    fleet_section["seats"] = build_seats(
        snapshot.online_agents,
        snapshot.state,
        snapshot.board,
        # The snapshot's own clock, so a seat's state is exact at the window's edge.
        now=float(hub_time) if isinstance(hub_time, int | float) else time.time(),
        liveness=snapshot.agent_liveness,
        wake_capabilities=snapshot.wake_capabilities,
    )
    risk = build_risk_view(fleet).to_dict()
    risk["guidance"] = build_risk_guidance(
        board=snapshot.board,
        manifest=snapshot.manifest,
        state=snapshot.state,
        safe_task_ids=fleet.tasks.ready,
    ).to_dict()
    headline = build_headline(
        snapshot.board,
        fleet_section,
        risk,
        build_observed_fleet_health({"observed_peers": list(observed_peers)}),
    )
    return {"fleet": fleet_section, "risk": risk, "headline": headline}
