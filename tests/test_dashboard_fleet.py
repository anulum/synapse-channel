# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fleet visibility dashboard tests

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.request import Request, urlopen

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import (
    DashboardSnapshot,
    render_dashboard_html,
    start_dashboard_server,
)
from synapse_channel.dashboard_fleet import build_fleet_visibility


def _http_get(url: str) -> tuple[int, str, str]:
    request = Request(url, headers={"Connection": "close"})
    with urlopen(request, timeout=3) as response:  # nosec B310
        return response.status, response.headers.get_content_type(), response.read().decode()


def _write_a2a_state(path: Path) -> None:
    store = A2ATaskStore(path)
    store.put(
        {
            "id": "a2a-open",
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"createdAt": 100.0, "updatedAt": 110.0},
        }
    )
    store.put(
        {
            "id": "a2a-done",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"createdAt": 90.0, "updatedAt": 95.0},
        }
    )
    store.put_push_config("a2a-open", {"url": "https://example.invalid/hook"})


def test_fleet_visibility_derives_snapshot_counts(tmp_path: Path) -> None:
    a2a_state = tmp_path / "a2a-state.json"
    _write_a2a_state(a2a_state)
    snapshot = DashboardSnapshot(
        online_agents=[
            "SYNAPSE-CHANNEL/codex-main",
            "SYNAPSE-CHANNEL/codex-main-rx",
            "SYNAPSE-CHANNEL/reviewer",
        ],
        state={
            "active_claims": [
                {
                    "task_id": "ACTIVE",
                    "owner": "SYNAPSE-CHANNEL/codex-main",
                    "lease_expires_at": 250.0,
                    "paths": ["src/synapse_channel/dashboard.py"],
                },
                {
                    "task_id": "STALE",
                    "owner": "SYNAPSE-CHANNEL/reviewer",
                    "lease_expires_at": 120.0,
                    "paths": ["tests/test_dashboard_fleet.py"],
                },
            ]
        },
        board={
            "tasks": [
                {"task_id": "READY", "title": "Ready", "status": "open", "depends_on": []},
                {
                    "task_id": "BLOCKED",
                    "title": "Blocked",
                    "status": "blocked",
                    "depends_on": ["READY"],
                },
            ],
            "ready": ["READY"],
            "progress": [
                {
                    "task_id": "ACTIVE",
                    "author": "SYNAPSE-CHANNEL/codex-main",
                    "kind": "assessment",
                    "text": "release receipt: evidence=pytest tests/test_dashboard_fleet.py -q",
                    "posted_at": 140.0,
                }
            ],
        },
        manifest=[],
    )

    fleet = build_fleet_visibility(snapshot, now=200.0, a2a_state_file=a2a_state).to_dict()

    assert fleet["agents"]["live"] == [
        "SYNAPSE-CHANNEL/codex-main",
        "SYNAPSE-CHANNEL/reviewer",
    ]
    assert fleet["agents"]["waiters"] == ["SYNAPSE-CHANNEL/codex-main-rx"]
    assert fleet["agents"]["missing_waiters"] == ["SYNAPSE-CHANNEL/reviewer-rx"]
    assert fleet["claims"]["active"] == 1
    assert fleet["claims"]["stale"] == 1
    assert fleet["tasks"]["ready"] == ["READY"]
    assert fleet["tasks"]["blocked"] == [{"task_id": "BLOCKED", "blocked_by": ["READY"]}]
    assert fleet["receipts"][0]["task_id"] == "ACTIVE"
    assert fleet["a2a"]["total"] == 2
    assert fleet["a2a"]["states"] == {
        "TASK_STATE_COMPLETED": 1,
        "TASK_STATE_FAILED": 1,
    }
    assert fleet["a2a"]["push_configs"] == 1


def test_fleet_visibility_renders_in_dashboard_html(tmp_path: Path) -> None:
    a2a_state = tmp_path / "a2a-state.json"
    _write_a2a_state(a2a_state)
    snapshot = DashboardSnapshot(
        online_agents=["demo", "demo-rx"],
        state={"active_claims": []},
        board={
            "tasks": [{"task_id": "BLOCKED", "status": "blocked", "depends_on": ["SETUP"]}],
            "ready": [],
            "progress": [
                {
                    "task_id": "BLOCKED",
                    "author": "demo",
                    "kind": "assessment",
                    "text": "release receipt: evidence=docs",
                }
            ],
        },
        manifest=[],
    )

    html = render_dashboard_html(snapshot, refresh_seconds=5, a2a_state_file=a2a_state)

    assert "Fleet visibility" in html
    assert "Missing waiters" in html
    assert "A2A tasks" in html
    assert "Release receipts" in html
    assert "TASK_STATE_FAILED" in html


def test_dashboard_parser_accepts_a2a_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a.json"

    args = cli.build_parser().parse_args(["dashboard", "--a2a-state-file", str(state_file)])

    assert args.a2a_state_file == state_file


async def test_dashboard_http_json_includes_fleet_visibility(tmp_path: Path) -> None:
    a2a_state = tmp_path / "a2a-state.json"
    _write_a2a_state(a2a_state)
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker = await connect_agent("SYNAPSE-CHANNEL/worker", uri)
        waiter = await connect_agent("SYNAPSE-CHANNEL/worker-rx", uri)
        try:
            await worker.agent.post_task("READY", title="Ready task")
            await worker.recorder.wait_for(
                lambda message: (
                    message.get("type") == "ledger_task_posted"
                    and message.get("task", {}).get("task_id") == "READY"
                )
            )
            await worker.agent.post_progress(
                "READY",
                "release receipt: evidence=pytest tests/test_dashboard_fleet.py -q",
                kind="assessment",
            )
            await worker.recorder.wait_for(
                lambda message: (
                    message.get("type") == "ledger_progress_posted"
                    and message.get("note", {}).get("task_id") == "READY"
                )
            )
            server = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="SYNAPSE-CHANNEL/dashboard",
                token=None,
                ready_timeout=1.0,
                response_timeout=1.0,
                refresh_seconds=5,
                allow_non_loopback=False,
                a2a_state_file=a2a_state,
            )
            try:
                status, content_type, body = await asyncio.to_thread(
                    _http_get, server.url("/snapshot.json")
                )
            finally:
                server.close()
        finally:
            await close_agents(worker, waiter)

    payload = json.loads(body)
    assert status == 200
    assert content_type == "application/json"
    assert "SYNAPSE-CHANNEL/worker" in payload["fleet"]["agents"]["live"]
    assert payload["fleet"]["agents"]["waiters"] == ["SYNAPSE-CHANNEL/worker-rx"]
    assert payload["fleet"]["tasks"]["ready"] == ["READY"]
    assert payload["fleet"]["receipts"][0]["task_id"] == "READY"
    assert payload["fleet"]["a2a"]["total"] == 2
