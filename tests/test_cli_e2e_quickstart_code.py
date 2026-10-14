# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real copy-paste quickstart and API journeys
"""Run the actual copy-paste Python flows from the quickstart and API guides.

``docs/quickstart.md`` and ``docs/api.md`` teach the same complete, confirmed
``SynapseAgent`` flow. Other doc tests only assert
that snippet's *text* is present. This journey extracts the exact fenced block,
points it at a throwaway hub, and runs it as a fresh subprocess — so the code a
reader pastes is proven to connect, claim, checkpoint, and release for real. It
also guards the ``connect()``-is-once-only shape the snippet was written to teach:
if the welcome-then-verbs ordering regresses, ``wait_until_ready`` returns false,
the snippet raises, and this test fails.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import isolated_hub, run_cli
from synapse_channel import MessageType, SynapseAgent

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_URI = "ws://localhost:8876"


def _event_query_until(
    db_path: Path, query: str, *, required: tuple[str, ...], timeout: float = 5.0
) -> str:
    """Poll an event-query until every required token appears in its output."""
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        result = run_cli("event-query", str(db_path), query)
        last_output = result.output
        if result.ok() and all(token in result.stdout for token in required):
            return result.stdout
        time.sleep(0.05)
    return last_output


def _coordinate_from_code_snippet(guide: str) -> str:
    """Return the complete coordination flow from the named public guide."""
    doc = (_REPO_ROOT / "docs" / guide).read_text(encoding="utf-8")
    heading = "## Coordinate from code" if guide == "quickstart.md" else "## The client in one flow"
    section = doc.split(heading, 1)
    assert len(section) == 2, f"{guide} is missing its coordination flow"
    match = re.search(r"```python\n(.*?)```", section[1], re.DOTALL)
    assert match is not None, "no python code block under 'Coordinate from code'"
    snippet = match.group(1)
    # The block is only worth running if it still teaches the whole verb path.
    for token in ("SynapseAgent", "wait_until_ready", "claim(", "save_checkpoint(", "release("):
        assert token in snippet, f"quickstart snippet no longer contains {token!r}"
    assert _DEFAULT_URI in snippet, "quickstart snippet no longer targets the default hub URI"
    return snippet


@pytest.mark.parametrize("require_fencing", [False, True])
@pytest.mark.parametrize("guide", ["quickstart.md", "api.md"])
def test_quickstart_coordinate_from_code_snippet_runs_against_a_hub(
    tmp_path: Path, require_fencing: bool, guide: str
) -> None:
    """The pasted quickstart coordinates and names its epoch on a strict hub."""
    snippet = _coordinate_from_code_snippet(guide)

    extra_args = ("--require-fencing-epoch",) if require_fencing else ()
    with isolated_hub(tmp_path, extra_args=extra_args) as hub:
        runnable = snippet.replace(_DEFAULT_URI, hub.uri)
        completed = subprocess.run(  # noqa: S603 - fixed interpreter, doc snippet
            [sys.executable, "-c", runnable],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        # verbose SynapseAgent prints its connection banner once ready.
        assert "Online" in completed.stdout, completed.stdout

        with sqlite3.connect(hub.db_path) as database:
            rows = database.execute(
                "SELECT payload FROM events WHERE kind='claim' ORDER BY seq"
            ).fetchall()
        claims = [json.loads(row[0]) for row in rows]
        assert len(claims) == 1, claims
        task_id = claims[0]["task_id"]
        assert task_id.startswith("refactor-parser-"), task_id
        timeline = _event_query_until(
            hub.db_path,
            f"task {task_id} timeline",
            required=("kind=claim", "kind=checkpoint", "kind=task_update", "kind=release"),
        )
        assert "kind=claim" in timeline, timeline
        assert "kind=checkpoint" in timeline, timeline
        assert "kind=task_update" in timeline, timeline
        assert "kind=release" in timeline, timeline
        assert "owner=ALPHA" in timeline, timeline


@pytest.mark.asyncio
@pytest.mark.parametrize("guide", ["quickstart.md", "api.md"])
async def test_quickstart_conflict_aborts_before_checkpoint(tmp_path: Path, guide: str) -> None:
    """A competing granted scope stops the actual snippet at its claim denial."""
    with isolated_hub(tmp_path, extra_args=("--require-fencing-epoch",)) as hub:
        frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def receive(frame: dict[str, Any]) -> None:
            """Retain the holder's operation replies."""
            frames.put_nowait(frame)

        holder = SynapseAgent("holder", receive, uri=hub.uri, verbose=False)
        connection = asyncio.create_task(holder.connect())
        try:
            assert await holder.wait_until_ready(5.0)
            await holder.claim("held-parser", paths=["src/parser"], ttl_seconds=60.0)
            while True:
                grant = await asyncio.wait_for(frames.get(), 5.0)
                if grant.get("type") == MessageType.CLAIM_GRANTED:
                    break
            assert grant.get("owner") == "holder"
            runnable = _coordinate_from_code_snippet(guide).replace(_DEFAULT_URI, hub.uri)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                runnable,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 15.0)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert process.returncode == 1, stderr.decode()
            assert "task operation refused or unexpected reply" in stderr.decode()
            with sqlite3.connect(hub.db_path) as database:
                rows = database.execute(
                    "SELECT kind, payload FROM events WHERE kind IN "
                    "('claim', 'checkpoint', 'task_update', 'release') ORDER BY seq"
                ).fetchall()
            assert [(kind, json.loads(payload)["task_id"]) for kind, payload in rows] == [
                ("claim", "held-parser")
            ]
        finally:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
