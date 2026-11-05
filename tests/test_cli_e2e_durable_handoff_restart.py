# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a restarted hub still names a handed-off claim
"""Prove a cold seat can resume a handoff from the journal alone.

A temporary hub admits a claim and a handoff. That process stops. A second
hub opens the same database. ``synapse state`` on the second hub must name
the successor, the claim, and the unfinished paths. The journal query must
show the same handoff. No field is added when those facts are already there.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import isolated_hub, run_cli
from synapse_channel import MessageType, SynapseAgent

_TASK = "HANDOFF"
_PATH = "src/app.py"
_BRANCH = "feature"


async def _wait(frames: asyncio.Queue[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    """Return the next frame that satisfies ``predicate``."""
    while True:
        frame = await asyncio.wait_for(frames.get(), 8.0)
        if predicate(frame):
            return frame


@pytest.mark.asyncio
async def test_restarted_hub_names_successor_claim_and_paths(tmp_path: Path) -> None:
    """A cold ``synapse state`` names the recipient, the task, and its paths."""
    with isolated_hub(tmp_path) as hub:
        alpha_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        beta_frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def take_alpha(frame: dict[str, Any]) -> None:
            alpha_frames.put_nowait(frame)

        async def take_beta(frame: dict[str, Any]) -> None:
            beta_frames.put_nowait(frame)

        alpha = SynapseAgent("ALPHA", take_alpha, uri=hub.uri, verbose=False)
        beta = SynapseAgent("BETA", take_beta, uri=hub.uri, verbose=False)
        alpha_conn = asyncio.create_task(alpha.connect())
        beta_conn = asyncio.create_task(beta.connect())
        try:
            assert await alpha.wait_until_ready(5.0)
            assert await beta.wait_until_ready(5.0)
            await alpha.claim(
                _TASK,
                note="unfinished",
                ttl_seconds=3600,
                paths=[_PATH],
                git={"branch": _BRANCH, "base": "main", "auto_release_on": "manual"},
            )
            grant = await _wait(
                alpha_frames,
                lambda frame: (
                    frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("task_id") == _TASK
                ),
            )
            assert grant.get("owner") == "ALPHA"
            await alpha.handoff(_TASK, "BETA", note="resume from the journal")
            handed = await _wait(
                beta_frames,
                lambda frame: (
                    frame.get("type") == MessageType.HANDOFF_GRANTED
                    and frame.get("task_id") == _TASK
                ),
            )
            assert handed.get("owner") == "BETA"
        finally:
            alpha.running = False
            beta.running = False
            for connection in (alpha_conn, beta_conn):
                connection.cancel()
            await asyncio.gather(alpha_conn, beta_conn, return_exceptions=True)
        database = hub.db_path

    with isolated_hub(tmp_path) as restarted:
        state = run_cli("state", "--name", "COLD", uri=restarted.uri, timeout=20)
    assert state.ok(), state.output
    assert f"{_TASK} " in state.stdout
    assert "owner=BETA" in state.stdout
    assert f"paths={_PATH}" in state.stdout
    assert f"git={_BRANCH}->main" in state.stdout

    queried = run_cli(
        "event-query",
        str(database),
        f"task {_TASK} timeline",
        "--json",
        timeout=20,
    )
    assert queried.ok(), queried.output
    records = json.loads(queried.stdout)["records"]
    handoffs = [row for row in records if row["kind"] == "handoff"]
    assert len(handoffs) == 1
    assert handoffs[0]["task_id"] == _TASK
    assert handoffs[0]["owner"] == "BETA"
    assert handoffs[0]["paths"] == [_PATH]
    assert handoffs[0]["payload"]["git"]["base"] == "main"
    assert handoffs[0]["payload"]["git"]["branch"] == _BRANCH
