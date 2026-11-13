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
from collections.abc import Callable
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


def _assert_seeded(hub: Any, rename: Callable[[str], str] = str) -> None:
    """Compare the hub's plan and claims with what the script says it seeds."""
    statuses = dict(fleet.BOARD_STATUSES)
    for task_id, title, depends_on in fleet.TASKS:
        task = hub.blackboard.tasks[rename(task_id)]
        assert task.title == title
        assert tuple(task.depends_on) == tuple(rename(needed) for needed in depends_on)
        assert task.status == statuses.get(task_id, "open")
        assert task.project == rename("harbour" if task_id.startswith("HARBOUR") else "lighthouse")
    assert len(hub.blackboard.tasks) == len(fleet.TASKS)
    for owner, task_id, paths in fleet.CLAIMS:
        claim = hub.state.claims[rename(task_id)]
        assert claim.owner == rename(owner)
        assert claim.status == "working"
        assert len(claim.paths) == len(paths)
    assert len(hub.state.claims) == len(fleet.CLAIMS)


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is a POSIX signal")
@pytest.mark.parametrize("long_names", [False, True])
async def test_the_script_seeds_a_real_hub_and_leaves_on_a_signal(long_names: bool) -> None:
    """As a child process the fleet joins, seeds, says so, and leaves cleanly when stopped."""
    rename: Callable[[str], str] = fleet.long_name if long_names else str
    async with running_hub() as (hub, uri):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SCRIPT),
            *(["--long-names"] if long_names else []),
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
                "participants": [rename(name) for name in fleet.PARTICIPANTS],
                "tasks": [rename(task_id) for task_id, _title, _needs in fleet.TASKS],
                "claims": [rename(task_id) for _owner, task_id, _paths in fleet.CLAIMS],
                "closed": _closed(rename),
                "finding": fleet.FINDING,
            }
            _assert_seeded(hub, rename)
            child.send_signal(signal.SIGTERM)
            _out, errors = await asyncio.wait_for(child.communicate(), timeout=30)
        finally:
            with contextlib.suppress(ProcessLookupError):
                child.kill()
            await child.wait()
        assert child.returncode == 0, errors.decode()
        assert errors == b""


def _assert_standing(hub: Any) -> None:
    """After the first act alone: the plan stands; the late task is neither claimed nor begun."""
    late = fleet.LATE_TASK
    assert set(hub.blackboard.tasks) == {task_id for task_id, _title, _needs in fleet.TASKS}
    assert hub.blackboard.tasks[late].status == "open"
    assert set(hub.state.claims) == {
        task_id for _owner, task_id, _paths in fleet.CLAIMS if task_id != late
    }


def _closed(rename: Callable[[str], str]) -> list[str]:
    """Return the closed tasks the script names, which a bounded snapshot counts without loading."""
    return [
        rename(task_id)
        for task_id, status in fleet.BOARD_STATUSES
        if status in {"done", "cancelled"}
    ]


def _line(act: int, claims: list[str]) -> dict[str, object]:
    """Return the line the script prints after one act."""
    return {
        "ready": True,
        "act": act,
        "participants": list(fleet.PARTICIPANTS),
        "tasks": [task_id for task_id, _title, _needs in fleet.TASKS],
        "claims": claims,
        "closed": _closed(str),
        "finding": fleet.FINDING,
    }


async def _two_act_child(uri: str, stdin: int) -> asyncio.subprocess.Process:
    """Start the script in two acts with the given standard input."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(SCRIPT),
        "--two-acts",
        uri,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_environment(),
    )


async def _leave(child: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    """Stop the child with a signal and return what else it printed.

    The child's standard input stays open until it has exited, so that the end
    of input cannot reach it before the signal does.
    """
    assert child.stdout is not None and child.stderr is not None
    try:
        child.send_signal(signal.SIGTERM)
        await asyncio.wait_for(child.wait(), timeout=30)
        return await child.stdout.read(), await child.stderr.read()
    finally:
        with contextlib.suppress(ProcessLookupError):
            child.kill()
        await child.wait()


ALL_CLAIMS = [task_id for _owner, task_id, _paths in fleet.CLAIMS]
EARLY_CLAIMS = [task_id for task_id in ALL_CLAIMS if task_id != fleet.LATE_TASK]


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is a POSIX signal")
async def test_two_acts_wait_for_a_line_between_them() -> None:
    """The first act leaves a standing state; a line on standard input starts the second."""
    async with running_hub() as (hub, uri):
        child = await _two_act_child(uri, asyncio.subprocess.PIPE)
        assert child.stdout is not None and child.stdin is not None
        first = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=60))
        assert first == _line(1, EARLY_CLAIMS)
        _assert_standing(hub)

        child.stdin.write(b"second act\n")
        await child.stdin.drain()
        second = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=60))
        assert second == _line(2, ALL_CLAIMS)
        _assert_seeded(hub)

        rest, errors = await _leave(child)
        assert (child.returncode, rest, errors) == (0, b"", b"")


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is a POSIX signal")
async def test_a_stop_during_the_pause_ends_the_fleet_without_a_second_act() -> None:
    """Stopped while it waits for the line, the fleet leaves; the hub keeps the standing state."""
    async with running_hub() as (hub, uri):
        child = await _two_act_child(uri, asyncio.subprocess.PIPE)
        assert child.stdout is not None
        first = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=60))
        assert first == _line(1, EARLY_CLAIMS)

        rest, errors = await _leave(child)
        assert (child.returncode, rest, errors) == (0, b"", b"")
        _assert_standing(hub)


@pytest.mark.skipif(sys.platform == "win32", reason="the stop is a POSIX signal")
async def test_an_input_that_has_ended_starts_the_second_act_at_once() -> None:
    """With no one to send a line the two acts follow each other, each with its own line."""
    async with running_hub() as (hub, uri):
        child = await _two_act_child(uri, asyncio.subprocess.DEVNULL)
        assert child.stdout is not None
        first = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=60))
        second = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=60))
        assert (first, second) == (_line(1, EARLY_CLAIMS), _line(2, ALL_CLAIMS))
        _assert_seeded(hub)

        rest, errors = await _leave(child)
        assert (child.returncode, rest, errors) == (0, b"", b"")


@pytest.mark.parametrize(
    "arguments", [[], ["--long-names"], ["--two-acts"], ["ws://one", "ws://two"]]
)
def test_the_script_refuses_anything_but_one_hub_address(arguments: list[str]) -> None:
    """Without exactly one hub address the script states its usage and exits with two."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        env=_child_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert (
        completed.stderr.strip()
        == "usage: python demo_fleet.py HUB_URI [--long-names] [--two-acts]"
    )


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


def test_long_names_differ_only_at_their_ends() -> None:
    """The long form keeps the seat, the project and the task number at the end of the name."""
    assert fleet.long_name("harbour") == "harbour-coastal-monitoring-programme"
    assert (
        fleet.long_name("harbour/api")
        == "harbour-coastal-monitoring-programme/autonomous-operations-seat-api"
    )
    assert fleet.long_name("LIGHT-204") == "LIGHT-COASTAL-OPERATIONS-WORK-PACKAGE-204"
    seats = [fleet.long_name(name) for name in fleet.PARTICIPANTS if name.startswith("harbour/")]
    assert len({name.rsplit("-", 1)[0] for name in seats}) == 1
    assert len(set(seats)) == len(seats)


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
