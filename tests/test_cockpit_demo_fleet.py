# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the cockpit's demonstration fleet against a real hub
"""Run the cockpit's demonstration fleet against real hubs.

The fleet script is started by a browser journey, which no Python run measures.
These tests run it as a child process and in process against real hubs, and
compare what the hub then holds with what the script says it seeds.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub

pytestmark = pytest.mark.real_hub

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "clients" / "cockpit" / "e2e" / "demo_fleet.py"


def _load_fleet() -> Any:
    """Load the demonstration fleet script as a module."""
    spec = importlib.util.spec_from_file_location("cockpit_demo_fleet_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fleet = _load_fleet()


def _child_environment() -> dict[str, str]:
    """Return an environment in which a child process imports this source tree."""
    env = os.environ.copy()
    src = str(ROOT / "src")
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not previous else src + os.pathsep + previous
    return env


def _unused_port() -> int:
    """Return a loopback port that nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _assert_seeded(hub: Any) -> None:
    """Compare the hub's plan and claims with what the script says it seeds."""
    statuses = dict(fleet.BOARD_STATUSES)
    for task_id, title, depends_on in fleet.TASKS:
        task = hub.blackboard.tasks[task_id]
        assert task.title == title
        assert tuple(task.depends_on) == depends_on
        assert task.status == statuses.get(task_id, "open")
        assert task.project == ("harbour" if task_id.startswith("HARBOUR") else "lighthouse")
    assert len(hub.blackboard.tasks) == len(fleet.TASKS)
    for owner, task_id, paths in fleet.CLAIMS:
        claim = hub.state.claims[task_id]
        assert claim.owner == owner
        assert claim.status == "working"
        assert len(claim.paths) == len(paths)
    assert len(hub.state.claims) == len(fleet.CLAIMS)


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is a POSIX signal")
async def test_the_script_seeds_a_real_hub_and_leaves_on_a_signal() -> None:
    """As a child process the fleet joins, seeds, says so, and leaves cleanly when stopped."""
    async with running_hub() as (hub, uri):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            uri,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_environment(),
        )
        try:
            assert child.stdout is not None
            line = await asyncio.wait_for(child.stdout.readline(), timeout=60)
            ready = json.loads(line)
            assert ready == {
                "ready": True,
                "participants": list(fleet.PARTICIPANTS),
                "tasks": [task_id for task_id, _title, _needs in fleet.TASKS],
                "claims": [task_id for _owner, task_id, _paths in fleet.CLAIMS],
            }
            _assert_seeded(hub)
            child.send_signal(signal.SIGTERM)
            _out, errors = await asyncio.wait_for(child.communicate(), timeout=30)
        finally:
            with contextlib.suppress(ProcessLookupError):
                child.kill()
            await child.wait()
        assert child.returncode == 0, errors.decode()
        assert errors == b""


def test_the_script_refuses_a_missing_hub_address() -> None:
    """Without exactly one argument the script states its usage and exits with two."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=_child_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.strip() == "usage: python demo_fleet.py HUB_URI"


async def test_the_fleet_leaves_by_itself_when_its_hold_runs_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With nothing to stop it the fleet holds for the stated time and then disconnects."""
    async with running_hub() as (hub, uri):
        assert await fleet.run_fleet(uri, hold_seconds=0.05) == 0
        _assert_seeded(hub)

    ready = json.loads(capsys.readouterr().out)
    assert ready["ready"] is True
    assert ready["participants"] == list(fleet.PARTICIPANTS)


async def test_the_fleet_says_who_could_not_join() -> None:
    """A hub that is not there is reported by the first participant's name, with no ready line."""
    uri = f"ws://127.0.0.1:{_unused_port()}"

    with pytest.raises(TimeoutError, match="harbour/planner did not join the hub"):
        await fleet.run_fleet(uri, join_seconds=0.2)


async def test_an_unconfirmed_step_carries_the_hubs_refusal() -> None:
    """The inbox reports the hub's last refusal, or says that there was none."""
    inbox = fleet.Inbox()
    await inbox({"type": "error", "payload": "Unknown progress kind 'finding'."})

    with pytest.raises(TimeoutError, match="Unknown progress kind 'finding'"):
        await inbox.wait_for(lambda message: message.get("type") == "claim_granted", timeout=0.05)
    with pytest.raises(TimeoutError, match="no refusal was received"):
        await fleet.Inbox().wait_for(lambda message: False, timeout=0.05)


async def test_a_confirmation_that_arrives_later_ends_the_wait() -> None:
    """A wait started before the confirmation returns when it arrives."""
    inbox = fleet.Inbox()
    await inbox({"type": "ledger_task_posted", "task": {"task_id": "HARBOUR-102"}})
    waiting = asyncio.create_task(
        inbox.wait_for(fleet._confirms("ledger_task_posted", "HARBOUR-101"), timeout=5)
    )
    await asyncio.sleep(0.01)
    assert not waiting.done()

    await inbox({"type": "ledger_task_posted", "task": {"task_id": "HARBOUR-101"}})
    await asyncio.wait_for(waiting, timeout=5)
