# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `syn locks` lease view

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.ergonomics import Identity
from synapse_channel.locks import (
    LeaseRow,
    _duration,
    build_rows,
    main,
    query_locks,
    render_locks,
)


def _identity() -> Identity:
    return Identity("SYNAPSE-CHANNEL", "SYNAPSE-CHANNEL/codex-1", "env", True)


def _snapshot() -> dict[str, Any]:
    return {
        "active_claims": [
            {
                "task_id": "SYNAPSE-CHANNEL:git",
                "owner": "SYNAPSE-CHANNEL/codex-1",
                "status": "claimed",
                "claimed_at": 100.0,
                "lease_expires_at": 460.0,
                "worktree": "SYNAPSE-CHANNEL:git",
                "paths": [],
                "checkpoint": "",
                "git": {"branch": "feature/x", "base": "main", "auto_release_on": "manual"},
            },
            {
                "task_id": "docs",
                "owner": "SYNAPSE-CHANNEL",
                "status": "in_progress",
                "claimed_at": 150.0,
                "lease_expires_at": 350.0,
                "worktree": "",
                "paths": ["docs/cli.md"],
                "checkpoint": "cursor=9",
                "git": None,
            },
            {
                "task_id": "OTHER:git",
                "owner": "OTHER/agent",
                "status": "claimed",
                "claimed_at": 200.0,
                "lease_expires_at": 500.0,
                "worktree": "OTHER:git",
                "paths": [],
                "checkpoint": "",
                "git": None,
            },
        ]
    }


def test_build_rows_filters_project_and_formats_scope_release_path() -> None:
    rows = build_rows(_snapshot(), project="SYNAPSE-CHANNEL", owner=None, now=220.0)

    assert rows == [
        LeaseRow(
            task_id="SYNAPSE-CHANNEL:git",
            owner="SYNAPSE-CHANNEL/codex-1",
            status="claimed",
            scope="mutex:SYNAPSE-CHANNEL:git",
            age="2m00s",
            remaining="4m00s",
            release_command=("synapse release SYNAPSE-CHANNEL:git --name SYNAPSE-CHANNEL/codex-1"),
            checkpoint="-",
            git="feature/x->main",
        ),
        LeaseRow(
            task_id="docs",
            owner="SYNAPSE-CHANNEL",
            status="in_progress",
            scope="worktree:default paths=docs/cli.md",
            age="1m10s",
            remaining="2m10s",
            release_command="synapse release docs --name SYNAPSE-CHANNEL",
            checkpoint="cursor=9",
            git="-",
        ),
    ]


def test_build_rows_owner_filter_overrides_project_filter() -> None:
    rows = build_rows(_snapshot(), project="SYNAPSE-CHANNEL", owner="OTHER", now=220.0)

    assert [row.task_id for row in rows] == ["OTHER:git"]


def test_build_rows_all_projects_and_hour_durations() -> None:
    rows = build_rows(_snapshot(), project=None, owner=None, now=4_000.0)

    assert [row.task_id for row in rows] == ["SYNAPSE-CHANNEL:git", "docs", "OTHER:git"]
    assert rows[0].age == "1h05m00s"
    assert rows[0].remaining == "0s"


def test_render_locks_prints_operator_view(capsys: pytest.CaptureFixture[str]) -> None:
    rows = build_rows(_snapshot(), project="SYNAPSE-CHANNEL", owner=None, now=220.0)

    render_locks(rows, label="SYNAPSE-CHANNEL", as_json=False)

    out = capsys.readouterr().out
    assert "Active leases in SYNAPSE-CHANNEL (2):" in out
    assert "SYNAPSE-CHANNEL:git [claimed]" in out
    assert "scope=mutex:SYNAPSE-CHANNEL:git" in out
    assert "release=synapse release docs --name SYNAPSE-CHANNEL" in out


def test_render_locks_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    rows = build_rows(_snapshot(), project="SYNAPSE-CHANNEL", owner=None, now=220.0)

    render_locks(rows, label="SYNAPSE-CHANNEL", as_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["label"] == "SYNAPSE-CHANNEL"
    assert payload["leases"][0]["task_id"] == "SYNAPSE-CHANNEL:git"


async def test_syn_locks_queries_real_hub(capsys: pytest.CaptureFixture[str]) -> None:
    identity = _identity()
    async with running_hub(SynapseHub()) as (_, uri):
        owned = await connect_agent("SYNAPSE-CHANNEL/codex-1", uri)
        other = await connect_agent("OTHER/agent", uri)
        await owned.agent.claim("SYNAPSE-CHANNEL:git", worktree="SYNAPSE-CHANNEL:git")
        await owned.recorder.wait_for(
            lambda message: (
                message.get("type") == "claim_granted"
                and message.get("task_id") == "SYNAPSE-CHANNEL:git"
            )
        )
        await other.agent.claim("OTHER:git", worktree="OTHER:git")
        await other.recorder.wait_for(
            lambda message: (
                message.get("type") == "claim_granted" and message.get("task_id") == "OTHER:git"
            )
        )
        try:
            code = await query_locks(identity, uri=uri)
        finally:
            await close_agents(owned, other)

    assert code == 0
    out = capsys.readouterr().out
    assert "Active leases in SYNAPSE-CHANNEL" in out
    assert "SYNAPSE-CHANNEL:git" in out
    assert "OTHER:git" not in out


async def test_syn_locks_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    code = await query_locks(_identity(), uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)

    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


def test_main_sync_wrapper_runs_async_query_for_ergonomics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(_identity(), ["--uri", f"ws://127.0.0.1:{_free_port()}", "--ready-timeout", "0.1"])
        == 1
    )
    assert "Could not reach hub" in capsys.readouterr().out


def test_syn_locks_alias_is_packaged() -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts: dict[str, Any] = pyproject["project"]["scripts"]

    assert scripts["syn-locks"] == "synapse_channel.ergonomics:alias_locks"


@pytest.mark.parametrize("path", [Path("README.md"), Path("docs/cli.md"), Path("docs/recipes.md")])
def test_syn_locks_is_documented(path: Path) -> None:
    assert "syn locks" in path.read_text(encoding="utf-8")


def test_build_rows_survives_hostile_claim_timestamps() -> None:
    """A claim carrying unusable timestamps renders as a zero-age row, never a crash."""
    snapshot = _snapshot()
    hostile = snapshot["active_claims"][0]
    hostile["claimed_at"] = {"bad": 1}
    hostile["lease_expires_at"] = float("nan")

    rows = build_rows(snapshot, project="SYNAPSE-CHANNEL", owner=None, now=220.0)

    row = rows[0]
    assert row.task_id == "SYNAPSE-CHANNEL:git"
    assert row.age == _duration(0.0)
    assert row.remaining == _duration(0.0)
