# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an unchanged fleet with held claims reads as unchanged
"""A real hub with held claims: the tag holds and the stream says 'unchanged'.

Before the stamps that change on every fetch were left out of the fingerprint,
a fleet holding claims sent its whole snapshot on every cycle (the first
journey with real participants, finding D-17).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import DashboardServer, start_dashboard_server

TOKEN = "unchanged-real-hub-secret"


def _get(
    server: DashboardServer, path: str, etag: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    headers = {"Authorization": f"Bearer {TOKEN}", "Connection": "close"}
    if etag is not None:
        headers["If-None-Match"] = etag
    request = Request(server.url(path), headers=headers)
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310 - owned loopback server
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


async def _confirmed(handle: AgentHandle, kind: str, task_id: str) -> None:
    def seen(message: dict[str, object]) -> bool:
        if message.get("type") != kind:
            return False
        if message.get("task_id") == task_id:
            return True
        nested = message.get("task") or message.get("note")
        return isinstance(nested, dict) and nested.get("task_id") == task_id

    await handle.recorder.wait_for(seen)


async def _fleet_holding_claims(uri: str) -> list[AgentHandle]:
    planner = await connect_agent("UNCHANGED/planner", uri)
    worker = await connect_agent("UNCHANGED/worker", uri)
    for task_id in ("HELD-1", "HELD-2", "WAITING"):
        await planner.agent.post_task(task_id, title=f"Task {task_id}")
        await _confirmed(planner, "ledger_task_posted", task_id)
    await planner.agent.post_progress("HELD-1", "started", kind="note")
    await _confirmed(planner, "ledger_progress_posted", "HELD-1")
    await worker.agent.claim(
        "HELD-1",
        paths=["src/held_one.py"],
        ttl_seconds=600.0,
        git={"branch": "feature/held-1", "base": "main"},
    )
    await _confirmed(worker, "claim_granted", "HELD-1")
    await planner.agent.claim("HELD-2", paths=["src/held_two.py"], ttl_seconds=600.0)
    await _confirmed(planner, "claim_granted", "HELD-2")
    return [planner, worker]


def _dashboard(uri: str) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri=uri,
        name="UNCHANGED/observer",
        token=None,
        ready_timeout=2.0,
        response_timeout=2.0,
        refresh_seconds=1,
        allow_non_loopback=False,
        dashboard_token=TOKEN,
    )


async def _with_server(check: Callable[[DashboardServer], None]) -> None:
    async with running_hub(SynapseHub(hub_id="syn-unchanged")) as (_, uri):
        handles = await _fleet_holding_claims(uri)
        server = _dashboard(uri)
        try:
            await asyncio.to_thread(check, server)
        finally:
            server.close()
            await close_agents(*handles)


async def test_snapshots_of_a_fleet_holding_claims_share_one_tag() -> None:
    """Two fetches differ in their stamps only; the second request gets 304."""

    def check(server: DashboardServer) -> None:
        status, headers, body = _get(server, "/snapshot.json")
        document = json.loads(body)
        assert status == 200
        assert len(document["state"]["active_claims"]) == 2
        status, repeat, again = _get(server, "/snapshot.json")
        assert status == 200 and again != body
        assert repeat["ETag"] == headers["ETag"]
        status, _, empty = _get(server, "/snapshot.json", etag=headers["ETag"])
        assert (status, empty) == (304, b"")

    await _with_server(check)


async def test_the_stream_of_a_fleet_holding_claims_says_unchanged() -> None:
    """The second cycle of an unchanged fleet carries no document."""

    def check(server: DashboardServer) -> None:
        status, _, body = _get(server, "/live.ndjson?cycles=2")
        frames = [json.loads(line) for line in body.splitlines()]
        snapshots = [frame for frame in frames if frame.get("channel") == "snapshot"]
        assert status == 200
        assert snapshots[0]["status"] == "live"
        assert len(snapshots[0]["data"]["state"]["active_claims"]) == 2
        assert snapshots[1]["status"] == "unchanged"
        assert "data" not in snapshots[1]

    await _with_server(check)
