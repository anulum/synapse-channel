# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub-to-HTTP Studio board-column acceptance

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from dashboard_helpers import _http_get
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.dashboard import start_dashboard_server


async def _post_task(
    handle: AgentHandle,
    task_id: str,
    title: str,
    *,
    depends_on: list[str] | None = None,
) -> None:
    await handle.agent.post_task(task_id, title=title, depends_on=depends_on or [])
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == "ledger_task_posted"
            and message.get("task", {}).get("task_id") == task_id
        )
    )


def _column_members(studio: Mapping[str, object]) -> dict[str, list[str]]:
    tasks = studio["tasks"]
    assert isinstance(tasks, Mapping)
    projection = tasks["columns"]
    assert isinstance(projection, Mapping)
    columns = projection["columns"]
    assert isinstance(columns, list)
    members: dict[str, list[str]] = {}
    for column in columns:
        assert isinstance(column, Mapping)
        rows = column["tasks"]
        assert isinstance(rows, list)
        members[str(column["id"])] = [
            str(row["task_id"]) for row in rows if isinstance(row, Mapping)
        ]
    return members


async def test_live_hub_tasks_and_claims_reach_studio_columns_and_assets() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        planner = await connect_agent("SYNAPSE-CHANNEL/board-columns-planner", uri)
        watcher = await connect_agent("SYNAPSE-CHANNEL/board-columns-watcher", uri)
        try:
            await _post_task(planner, "READY", "Ready task")
            await _post_task(planner, "WORKING", "Working task")
            await _post_task(
                planner,
                "BLOCKED",
                "Blocked task",
                depends_on=["WORKING"],
            )
            await _post_task(planner, "DONE", "Done task")

            await planner.agent.update_ledger_task("WORKING", status="in_progress")
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "ledger_task_updated"
                    and message.get("task", {}).get("task_id") == "WORKING"
                )
            )
            await planner.agent.update_ledger_task("BLOCKED", status="blocked")
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "ledger_task_updated"
                    and message.get("task", {}).get("task_id") == "BLOCKED"
                )
            )
            await planner.agent.update_ledger_task("DONE", status="done")
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "ledger_task_updated"
                    and message.get("task", {}).get("task_id") == "DONE"
                )
            )

            await planner.agent.claim("WORKING", paths=["src/working.py"])
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "claim_granted" and message.get("task_id") == "WORKING"
                )
            )
            await planner.agent.update_task("WORKING", status="working")
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "task_updated" and message.get("task_id") == "WORKING"
                )
            )
            await planner.agent.claim("AD-HOC", note="Undeclared maintenance", paths=["docs/"])
            await watcher.recorder.wait_for(
                lambda message: (
                    message.get("type") == "claim_granted" and message.get("task_id") == "AD-HOC"
                )
            )

            server = start_dashboard_server(
                host="127.0.0.1",
                port=0,
                uri=uri,
                name="SYNAPSE-CHANNEL/board-columns-dashboard",
                token=None,
                ready_timeout=1.0,
                response_timeout=1.0,
                refresh_seconds=5,
                allow_non_loopback=False,
            )
            try:
                studio_response = await asyncio.to_thread(_http_get, server.url("/studio.json"))
                command_response = await asyncio.to_thread(_http_get, server.url("/studio/command"))
                asset_responses = {
                    path: await asyncio.to_thread(_http_get, server.url("/" + path))
                    for path in (
                        "board-columns.css",
                        "board-columns.js",
                        "studio-command.css",
                        "studio-command.js",
                        "studio-feeds.js",
                    )
                }
            finally:
                server.close()
        finally:
            await close_agents(planner, watcher)

    studio_status, studio_type, studio_body = studio_response
    assert (studio_status, studio_type) == (200, "application/json")
    studio = json.loads(studio_body)
    assert _column_members(studio) == {
        "open": ["READY"],
        "claimed": ["AD-HOC"],
        "working": ["WORKING"],
        "input_required": [],
        "blocked": ["BLOCKED"],
        "closed": ["DONE"],
        "other": [],
    }
    projection = studio["tasks"]["columns"]
    assert projection["declared_tasks"] == 4
    assert projection["ad_hoc_claims"] == 1
    claimed = next(
        row
        for column in projection["columns"]
        for row in column["tasks"]
        if row["task_id"] == "AD-HOC"
    )
    assert claimed["declared"] is False
    assert claimed["title"] == "Undeclared maintenance"

    command_status, command_type, command_body = command_response
    assert (command_status, command_type) == (200, "text/html")
    assert 'id="cc-board-columns"' in command_body
    assert 'src="/board-columns.js"' in command_body
    assert 'src="/studio-feeds.js"' in command_body
    for path, response in asset_responses.items():
        status, content_type, body = response
        assert status == 200, path
        assert content_type == ("text/css" if path.endswith(".css") else "text/javascript")
        assert "SYNAPSE_CHANNEL" in body
