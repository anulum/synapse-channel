# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a peer hub's observed state as an advisory scope of the dashboard
"""Show one observed peer hub in the dashboard's document shape, marked advisory.

A dashboard started with ``--observed-peer`` pulls each peer's event log on every
snapshot and folds it (:mod:`synapse_channel.observed_peers`). A ``peer:<hub id>``
scope shows that fold alone: the peer's board as the tasks, its observed claims as
the active claims, its progress notes. Nothing of the local hub enters it, and
nothing in it grants anything: the document, and every task and claim row in it,
says ``advisory``. An event log is not a roster, so a peer document has no online
agents; the claim owners it saw are listed in its ``peer`` block.

A peer the dashboard does not observe, or one whose pull failed, gives an empty
document that says why, never the rows of an earlier pull.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from synapse_channel.core.multihub_fold import ObservedState

if TYPE_CHECKING:
    from synapse_channel.dashboard import DashboardSnapshot
    from synapse_channel.observed_peers import ObservedPeerSnapshot

NOT_OBSERVED = "this dashboard does not observe that hub"
"""The reason a ``peer`` block gives for a hub id no ``--observed-peer`` names."""


def observed_peer(snapshot: DashboardSnapshot, hub_id: str) -> ObservedPeerSnapshot | None:
    """Return the pulled state of the peer ``hub_id``, or ``None`` when it is not observed."""
    return next((peer for peer in snapshot.observed_peers if peer.hub_id == hub_id), None)


def _advisory(record: Mapping[str, Any], task_id: str, hub_id: str) -> dict[str, Any]:
    """Return ``record`` for ``task_id`` marked as observed from ``hub_id``."""
    return {**record, "task_id": task_id, "advisory": True, "observed_from": hub_id}


def peer_snapshot(snapshot: DashboardSnapshot, hub_id: str) -> DashboardSnapshot:
    """Return a snapshot of the observed peer ``hub_id`` alone, in the local snapshot's shape.

    The peer's board records are the tasks, its observed claims the active claims,
    and its progress notes the progress; every task and claim is marked advisory.
    No local agent, role, claim, task or card is kept. A peer that is not observed,
    or whose pull failed, has an empty board.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        The whole snapshot the dashboard fetched, with its observed peers.
    hub_id : str
        The peer hub to show.
    """
    peer = observed_peer(snapshot, hub_id)
    state = peer.state if peer is not None and peer.reachable else ObservedState()
    tasks = [_advisory(record, task_id, hub_id) for task_id, record in state.board.items()]
    claims = [
        _advisory(claim.claim, task_id, hub_id) for task_id, claim in state.observed_claims.items()
    ]
    progress = [dict(note) for note in state.progress]
    return replace(
        snapshot,
        online_agents=[],
        state={"active_claims": claims, "agents": []},
        board={"tasks": tasks, "ready": [], "progress": progress},
        manifest=[],
        hub_version="",
        hub_id=hub_id,
        config_epoch="",
        agent_roles={},
        observed_peers=(),
        agent_liveness={},
        wake_capabilities={},
    )


def peer_block(snapshot: DashboardSnapshot, hub_id: str) -> dict[str, Any]:
    """Return what the dashboard knows of the peer ``hub_id``: whether it is observed and reached.

    Parameters
    ----------
    snapshot : DashboardSnapshot
        The whole snapshot the dashboard fetched, with its observed peers.
    hub_id : str
        The peer hub the scope names.
    """
    peer = observed_peer(snapshot, hub_id)
    if peer is None:
        return {"hub_id": hub_id, "observed": False, "reachable": False, "error": NOT_OBSERVED}
    return {
        "hub_id": hub_id,
        "observed": True,
        "reachable": peer.reachable,
        "cursor": peer.cursor,
        "log_end_seq": peer.log_end_seq,
        "lag": peer.lag,
        "clock_skew_seconds": peer.clock_skew_seconds,
        "observed_agents": list(peer.observed_agents),
        "board_conflicts": len(peer.state.board_conflicts),
        "error": peer.error,
    }
