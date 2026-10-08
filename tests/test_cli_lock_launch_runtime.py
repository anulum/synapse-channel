# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — locked command launch runtime journeys
"""Verify real executable launch outcomes and durable lock release through CLI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


@pytest.mark.asyncio
@pytest.mark.real_hub
@pytest.mark.parametrize("failure", ["missing", "permission", "format", "interpreter", "directory"])
async def test_launch_failure_releases_durable_lock(tmp_path: Path, failure: str) -> None:
    """Actual OS launch refusals report their status and allow the next lock holder."""
    executable = tmp_path / "wrapped command"
    if failure == "permission":
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o600)
    elif failure == "format":
        executable.write_text("not an executable format\n", encoding="utf-8")
        executable.chmod(0o700)
    elif failure == "interpreter":
        executable.write_text("#!./absent-interpreter\n", encoding="utf-8")
        executable.chmod(0o700)
    elif failure == "directory":
        executable.mkdir()

    journal = EventStore(tmp_path / "hub.db")
    task_id = f"launch-{failure}"
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                task_id,
                "--name",
                "launch/first",
                "--",
                str(executable),
                "argument-must-not-be-printed",
                uri=uri,
                cwd=tmp_path,
            )
            assert result.returncode == (127 if failure in {"missing", "interpreter"} else 126), (
                result.output
            )
            assert "lock: cannot execute" in result.stderr
            assert repr(str(executable)) in result.stderr
            assert "argument-must-not-be-printed" not in result.stderr
            assert "Traceback" not in result.stderr
            assert not result.stdout.strip()
            assert task_id not in hub.state.claims
            events = [row for row in journal.iter_events() if row.payload.get("task_id") == task_id]
            assert [row.kind for row in events] == ["claim", "release"]
            assert events[0].payload.get("owner") == "launch/first"

            next_holder = await asyncio.to_thread(
                run_cli,
                "lock",
                task_id,
                "--name",
                "launch/next",
                "--wait-timeout",
                "0",
                "--",
                sys.executable,
                "-c",
                "print('next-holder-ran'); raise SystemExit(7)",
                uri=uri,
            )
            assert next_holder.returncode == 7, next_holder.output
            assert next_holder.stdout.strip() == "next-holder-ran"
            assert not next_holder.stderr.strip()
            assert task_id not in hub.state.claims
            events = [row for row in journal.iter_events() if row.payload.get("task_id") == task_id]
            assert [row.kind for row in events] == ["claim", "release", "claim", "release"]
            assert [row.payload.get("owner") for row in events if row.kind == "claim"] == [
                "launch/first",
                "launch/next",
            ]
    finally:
        journal.close()
