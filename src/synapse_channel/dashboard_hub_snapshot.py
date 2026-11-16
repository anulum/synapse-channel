# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the hub snapshot one dashboard reads
"""Read the hub's roster, state, board and manifest through its public requests.

:func:`fetch_dashboard_snapshot` connects as one named client, asks for the four
read-side snapshots, waits for each within a bound, adds the observed peers'
advisory state when the start names them, and returns one
:class:`DashboardSnapshot`. :mod:`synapse_channel.dashboard` re-exports every
public name here, and its handler reads the fetcher through that module, so a
replacement there is still honoured.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard_views import derived_views
from synapse_channel.observed_peers import (
    ObservedPeerSnapshot,
    ObservedPeerSpec,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    observed_peers_to_dict,
)

SnapshotMapping = dict[str, Any]
"""Mutable mapping shape used for hub snapshot payloads."""

ManifestCards = list[dict[str, Any]]
"""List shape used by the hub capability manifest snapshot."""


class DashboardUnavailable(RuntimeError):
    """Raised when the dashboard cannot fetch the live hub snapshot."""


@dataclass(frozen=True)
class DashboardSnapshot:
    """Read-side data rendered by the local dashboard.

    Parameters
    ----------
    online_agents : list[str]
        Agent names currently present in the hub roster.
    state : dict[str, Any]
        Raw state snapshot returned by the hub.
    board : dict[str, Any]
        Raw shared-board snapshot returned by the hub.
    manifest : list[dict[str, Any]]
        Capability cards returned by the hub.
    hub_version : str
        Package version the live hub reports, for a cockpit pinning indicator.
        Empty when the hub predates the field.
    hub_id : str
        Hub identifier reported by the welcome frame. Empty only if the client
        connects to a hub old enough not to send the field.
    config_epoch : str
        Fingerprint of the hub's configuration posture, for the same indicator.
        Empty when the hub predates the field or was built without a config.
    agent_roles : dict[str, list[str]]
        The ``<project>/<role>`` roles each online agent answers to, mirrored from
        the hub's ``who`` snapshot so the cockpit can show role bindings alongside
        the roster. Empty for an agent that declared none, or for a hub that
        predates role addressing.
    observed_peers : tuple[ObservedPeerSnapshot, ...]
        Optional advisory peer snapshots fetched through the multi-hub log
        request path. Empty unless the dashboard was started with observed peers.
    agent_liveness : dict[str, Any]
        The ``who`` snapshot's ``agent_liveness`` as the hub sent it: whether each
        online agent is proven live, has a live waiter, and how long ago it last
        reacted. Empty when the hub tracks no reactions. The seat projection reads
        it; the document leaves it out, because its ages change on every fetch.
    wake_capabilities : dict[str, Any]
        The ``who`` snapshot's ``wake_capabilities``: the capability each online
        name declared. The fleet and seat projections read it to leave presence
        holders out; the document carries only their count (``headline``).
    observed_from : str
        The observed peer hub this snapshot shows (:mod:`synapse_channel.dashboard_peer_scope`);
        empty for this hub's own. The seat projection reads it, so a peer's seats
        claim no listening state; the document leaves it out, because a peer's
        document says so in its ``advisory`` and ``peer`` blocks.
    """

    online_agents: list[str]
    state: SnapshotMapping
    board: SnapshotMapping
    manifest: ManifestCards
    hub_version: str = ""
    hub_id: str = ""
    config_epoch: str = ""
    agent_roles: dict[str, list[str]] = field(default_factory=dict)
    observed_peers: tuple[ObservedPeerSnapshot, ...] = ()
    agent_liveness: dict[str, Any] = field(default_factory=dict)
    wake_capabilities: dict[str, Any] = field(default_factory=dict)
    observed_from: str = ""

    def to_dict(self, *, a2a_state_file: str | Path | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for HTTP responses.

        Parameters
        ----------
        a2a_state_file : str, pathlib.Path, or None, optional
            Optional persisted A2A bridge state file used to populate the
            derived ``fleet.a2a`` summary.
        """
        payload = asdict(self)
        # Each seat row carries its own part of the hub's proof (fleet.seats[].reaction).
        del payload["agent_liveness"]
        del payload["wake_capabilities"]
        # A peer's document says so in its own ``advisory`` and ``peer`` blocks.
        del payload["observed_from"]
        payload["observed_peers"] = observed_peers_to_dict(self.observed_peers)
        payload.update(
            derived_views(self, payload["observed_peers"], a2a_state_file=a2a_state_file)
        )
        return payload


def _mapping_from_who(who: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return one mapping field of a ``who`` snapshot, or an empty map when absent or malformed."""
    raw = who.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _who_fields(who: Mapping[str, Any]) -> dict[str, Any]:
    """Return the snapshot fields a ``who`` answer carries: the roster and the hub's word on it."""
    return {
        "online_agents": [str(agent_name) for agent_name in who.get("online_agents", [])],
        "hub_version": str(who.get("hub_version", "")),
        "config_epoch": str(who.get("config_epoch", "")),
        "agent_roles": _agent_roles_from_who(who),
        "agent_liveness": _mapping_from_who(who, "agent_liveness"),
        "wake_capabilities": _mapping_from_who(who, "wake_capabilities"),
    }


def _agent_roles_from_who(who: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return the roster's role bindings from a ``who`` snapshot, defensively.

    Each agent name maps to the ``<project>/<role>`` names it answers to. A
    non-mapping ``agent_roles`` field yields an empty map, a non-list binding for
    one agent is dropped, and names and roles are string-coerced — so a malformed
    or version-skewed hub snapshot degrades to an empty or partial map rather than
    propagating bad types into the served dashboard document.
    """
    raw = who.get("agent_roles", {})
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): [str(role) for role in roles]
        for name, roles in raw.items()
        if isinstance(roles, list)
    }


async def fetch_dashboard_snapshot(
    *,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float = 5.0,
    response_timeout: float = 2.0,
    observed_peers: tuple[ObservedPeerSpec, ...] = (),
    observed_token: str | None = None,
    observed_timeout: float = 10.0,
    observed_pins: dict[str, str] | None = None,
) -> DashboardSnapshot:
    """Fetch roster, state, board, and manifest snapshots from a live hub.

    Parameters
    ----------
    uri, name : str
        Hub URI and dashboard client identity.
    token : str or None
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub welcome handshake.
    response_timeout : float, optional
        Seconds to wait for every read-side snapshot response.
    observed_peers : tuple[ObservedPeerSpec, ...], optional
        Peer hubs to fetch through the multi-hub log path and render as
        advisory observed state.
    observed_pins : dict[str, str] or None, optional
        Per-hub ``sha256:<hex>`` certificate pins for self-signed ``wss://``
        observed peers; a pinned pull fails closed on any mismatch.

    Returns
    -------
    DashboardSnapshot
        Snapshot values gathered through public hub request messages.

    Raises
    ------
    DashboardUnavailable
        If the hub is unreachable or does not return every requested snapshot.
    """
    messages: dict[str, dict[str, Any]] = {}
    expected = {
        MessageType.WHO_SNAPSHOT,
        MessageType.STATE_SNAPSHOT,
        MessageType.BOARD_SNAPSHOT,
        MessageType.MANIFEST_SNAPSHOT,
    }

    async def collect(data: dict[str, Any]) -> None:
        message_type = str(data.get("type", ""))
        if message_type in expected:
            messages[message_type] = data

    agent = SynapseAgent(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            raise DashboardUnavailable(f"could not reach hub at {uri}")
        await agent.request_who()
        await agent.request_state()
        await agent.request_board()
        await agent.request_manifest()
        deadline = time.monotonic() + max(0.0, response_timeout)
        while set(messages) != expected and time.monotonic() < deadline:
            await asyncio.sleep(0.025)
        missing = sorted(expected.difference(messages))
        if missing:
            joined = ", ".join(missing)
            raise DashboardUnavailable(f"hub did not return dashboard snapshot(s): {joined}")
        who = messages[MessageType.WHO_SNAPSHOT]
        observed = await fetch_observed_peers(
            observed_peers,
            fetcher_factory=network_observed_fetcher_factory(
                local_id=f"{name}-observed",
                token=observed_token,
                timeout=observed_timeout,
                pins=observed_pins,
            ),
        )
        return DashboardSnapshot(
            state=dict(messages[MessageType.STATE_SNAPSHOT].get("snapshot", {})),
            board=dict(messages[MessageType.BOARD_SNAPSHOT].get("board", {})),
            manifest=[
                dict(card)
                for card in messages[MessageType.MANIFEST_SNAPSHOT].get("manifest", [])
                if isinstance(card, Mapping)
            ],
            hub_id=agent.hub_id if agent.hub_id != "unknown" else "",
            observed_peers=observed,
            **_who_fields(who),
        )
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task
