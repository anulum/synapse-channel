# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — golden path journalled through a temporary hub
"""Prove the shipped claim, refusal, handoff, and receipt on one journal.

The public first hour is ``synapse doctor`` then ``synapse demo``. This test
drives the same facts through a temporary ``synapse hub``: a live claim, the
documented file-scope refusal from ``synapse lock``, an atomic handoff, and a
``synapse release`` receipt that names that claim. It fails if any of those
four journal rows is missing or names a different task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run, isolated_hub, run_cli
from synapse_channel import MessageType, SynapseAgent
from synapse_channel.git.ordinary_claim import resolve_ordinary_claim_scope

_TASK = "GOLDEN"
_OVERLAP = "OVERLAP"
_PATH = "src/app.py"
_REFUSAL = "file scope conflicts"


def _rows(db_path: Path, kind: str) -> list[dict[str, Any]]:
    """Return decoded journal payloads of one event kind, in sequence order."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        found = connection.execute(
            "SELECT payload FROM events WHERE kind = ? ORDER BY seq",
            (kind,),
        ).fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in found]


async def _wait(frames: asyncio.Queue[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    """Return the next frame that satisfies ``predicate``."""
    while True:
        frame = await asyncio.wait_for(frames.get(), 8.0)
        if predicate(frame):
            return frame


@pytest.mark.asyncio
async def test_golden_path_journal_names_one_claim(tmp_path: Path) -> None:
    """A temporary hub records the claim, the refusal, the handoff, and the receipt."""
    repo = git_repo(tmp_path / "repo")
    source = repo / _PATH
    source.parent.mkdir()
    source.write_text("status = 'held'\n", encoding="utf-8")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-q", "-m", "add the claimed file")
    previous = Path.cwd()
    os.chdir(repo)
    try:
        scope = resolve_ordinary_claim_scope([_PATH])
    finally:
        os.chdir(previous)
    assert scope is not None

    with isolated_hub(tmp_path) as hub:
        connected = run_cli("who", uri=hub.uri)
        assert connected.ok(), connected.output
        assert "Online" in connected.stdout

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
                worktree=scope.worktree,
                paths=list(scope.paths),
                path_identity=scope.path_identity,
            )
            grant = await _wait(
                alpha_frames,
                lambda frame: (
                    frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("task_id") == _TASK
                ),
            )
            assert grant.get("owner") == "ALPHA"

            refused = run_cli(
                "lock",
                _OVERLAP,
                "--name",
                "INTERLOPER",
                "--paths",
                _PATH,
                "--wait-timeout",
                "0",
                "--",
                "true",
                uri=hub.uri,
                cwd=repo,
                timeout=20,
            )
            assert refused.returncode == 1, refused.output
            assert _REFUSAL in refused.output
            assert f"Could not acquire lock '{_OVERLAP}'" in refused.output

            await alpha.handoff(_TASK, "BETA", note="ready for the receipt")
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

        receipt = run_cli(
            "release",
            _TASK,
            "--name",
            "BETA",
            "--evidence",
            "observed check passed",
            "--receipt-json",
            uri=hub.uri,
            timeout=20,
        )
        assert receipt.ok(), receipt.output
        printed = json.loads(receipt.stdout)
        assert printed["task_id"] == _TASK
        assert "observed check passed" in printed["evidence"]

        claims = [row for row in _rows(hub.db_path, "claim") if row.get("task_id") == _TASK]
        assert len(claims) == 1, claims
        assert claims[0]["owner"] == "ALPHA"
        assert list(scope.paths) == claims[0]["paths"]

        denial_task = hashlib.sha256(_OVERLAP.encode("utf-8")).hexdigest()
        denials = [
            row
            for row in _rows(hub.db_path, "claim_denial")
            if row.get("task_id_sha256") == denial_task
        ]
        assert len(denials) == 1, denials
        assert denials[0]["reason_code"] == "SCOPE_CONFLICT"
        assert denials[0]["decision"] == "deny"

        handoffs = [row for row in _rows(hub.db_path, "handoff") if row.get("task_id") == _TASK]
        assert len(handoffs) == 1, handoffs
        assert handoffs[0]["owner"] == "BETA"

        releases = [row for row in _rows(hub.db_path, "release") if row.get("task_id") == _TASK]
        assert len(releases) == 1, releases

        timeline = run_cli("event-query", str(hub.db_path), f"task {_TASK} timeline")
        assert timeline.ok(), timeline.output
        for marker in ("kind=claim", "kind=handoff", "kind=release", f"task={_TASK}"):
            assert marker in timeline.stdout, timeline.stdout
