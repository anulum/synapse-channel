# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a real observed hub served as an advisory scope by a real dashboard
"""Two real hubs, real agents on each, and the production dashboard observing one.

The dashboard's own hub holds one task; the observed hub holds two and a claim.
The observed hub's scope must show its rows alone, each advisory, with its pull
stated; a peer that does not answer must show no row.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hub_e2e_helpers import (
    AgentHandle,
    authorised_multihub_serving_policy,
    close_agents,
    connect_agent,
    free_port,
    running_hub,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.observed_peers import ObservedPeerSpec

TOKEN = "peer-scope-secret"
OBSERVER = "WEST/dashboard-observed"
"""The identity the dashboard named ``WEST/dashboard`` pulls a peer's log as."""


def _confirms(kind: str, task_id: str) -> Callable[[dict[str, Any]], bool]:
    """Return a matcher for the hub's ``kind`` confirmation of ``task_id``."""

    def matches(message: dict[str, Any]) -> bool:
        """Return whether ``message`` is that confirmation."""
        task = message.get("task")
        nested = task.get("task_id") if isinstance(task, dict) else None
        return message.get("type") == kind and task_id in (message.get("task_id"), nested)

    return matches


async def _post(handle: AgentHandle, task_id: str, project: str) -> None:
    """Declare ``task_id`` in ``project`` through the agent and wait for the hub."""
    await handle.agent.post_task(task_id, f"{task_id} title", project=project)
    await handle.recorder.wait_for(_confirms("ledger_task_posted", task_id))


def _get(server: DashboardServer, path: str) -> tuple[int, bytes]:
    """Return the status and body of an authorised request to the dashboard."""
    request = Request(
        server.url(path), headers={"Connection": "close", "Authorization": f"Bearer {TOKEN}"}
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def _start(local_uri: str, peer_uri: str) -> DashboardServer:
    """Start the production dashboard on the local hub, observing the peer and a silent hub."""
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri=local_uri,
        name="WEST/dashboard",
        token=None,
        dashboard_token=TOKEN,
        ready_timeout=5.0,
        response_timeout=5.0,
        refresh_seconds=5,
        allow_non_loopback=False,
        observed_peers=(
            ObservedPeerSpec("east", peer_uri),
            ObservedPeerSpec("gone", f"ws://127.0.0.1:{free_port()}"),
        ),
        observed_timeout=5.0,
    )


async def _read(server: DashboardServer) -> dict[str, tuple[int, bytes]]:
    """Read the peer, the silent peer and the local snapshot, Studio, and the live channel."""
    paths = {
        "east": "/snapshot.json?scope=" + quote("peer:east"),
        "gone": "/snapshot.json?scope=" + quote("peer:gone"),
        "local": "/snapshot.json",
        "live": "/live.ndjson?cycles=1&scope=" + quote("peer:east"),
        "studio_east": "/studio.json?scope=" + quote("peer:east"),
        "studio_local": "/studio.json",
    }
    return {key: await asyncio.to_thread(_get, server, path) for key, path in paths.items()}


async def _serve_two_hubs(store_path: Path) -> dict[str, tuple[int, bytes]]:
    """Run both hubs and their agents, and read what the dashboard serves."""
    store = EventStore(store_path)
    try:
        async with (
            running_hub(SynapseHub(hub_id="west")) as (_local, local_uri),
            running_hub(
                SynapseHub(
                    hub_id="east",
                    journal=store,
                    multihub_serving_policy=authorised_multihub_serving_policy(OBSERVER),
                )
            ) as (_peer, peer_uri),
        ):
            west = await connect_agent("WEST/planner", local_uri)
            east = await connect_agent("EAST/api", peer_uri)
            try:
                await _post(west, "W1", "WEST")
                for task_id in ("E1", "E2"):
                    await _post(east, task_id, "EAST")
                await east.agent.claim("E1", paths=["src/upload.py"])
                await east.recorder.wait_for(_confirms("claim_granted", "E1"))
                server = await asyncio.to_thread(_start, local_uri, peer_uri)
                try:
                    return await _read(server)
                finally:
                    await asyncio.to_thread(server.close)
            finally:
                await close_agents(west, east)
    finally:
        store.close()


def _assert_peer(east: dict[str, Any]) -> None:
    """The observed hub's document holds its rows alone, each advisory, and states its pull."""
    assert east["advisory"] is True and east["hub_id"] == "east"
    assert sorted(task["task_id"] for task in east["board"]["tasks"]) == ["E1", "E2"]
    claims = east["state"]["active_claims"]
    assert [(claim["task_id"], claim["owner"]) for claim in claims] == [("E1", "EAST/api")]
    rows = [*east["board"]["tasks"], *claims]
    assert {(row["advisory"], row["observed_from"]) for row in rows} == {(True, "east")}
    peer = east["peer"]
    assert (peer["observed"], peer["reachable"], peer["observed_agents"]) == (
        True,
        True,
        ["EAST/api"],
    )
    assert peer["cursor"] > 0 and peer["lag"] == 0 and peer["error"] == ""
    assert east["scope"]["peers"] == ["east", "gone"] and east["online_agents"] == []


def _assert_live_and_studio(answers: dict[str, tuple[int, bytes]], east: dict[str, Any]) -> None:
    """The live channel and Studio serve the same peer scope, and Studio keeps its marks."""
    frames = [json.loads(line) for line in answers["live"][1].splitlines()]
    live = next(frame for frame in frames if frame.get("channel") == "snapshot")["data"]
    # Each pull measures the peer's clock afresh, so the two renders are compared by their rows.
    assert (live["advisory"], live["scope"]) == (True, east["scope"])
    assert live["board"]["tasks"] == east["board"]["tasks"]
    assert live["state"]["active_claims"] == east["state"]["active_claims"]
    studio = json.loads(answers["studio_east"][1])
    assert (studio["advisory"], studio["peer"]["hub_id"], studio["hub"]["id"]) == (
        True,
        "east",
        "east",
    )
    assert studio["scope"]["scope"] == "peer:east"
    local_studio = json.loads(answers["studio_local"][1])
    assert "advisory" not in local_studio and "peer" not in local_studio


async def test_a_real_observed_hub_is_an_advisory_scope_and_a_silent_one_shows_nothing(
    tmp_path: Path,
) -> None:
    answers = await _serve_two_hubs(tmp_path / "east.db")
    assert {key: status for key, (status, _body) in answers.items()} == dict.fromkeys(answers, 200)
    east, gone, local = (json.loads(answers[key][1]) for key in ("east", "gone", "local"))

    _assert_peer(east)
    assert gone["board"]["tasks"] == [] and gone["state"]["active_claims"] == []
    assert gone["peer"]["reachable"] is False and gone["peer"]["error"]
    assert [task["task_id"] for task in local["board"]["tasks"]] == ["W1"]
    assert "advisory" not in local
    _assert_live_and_studio(answers, east)
