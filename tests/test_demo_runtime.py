# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for golden-demo runtime boundaries

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

from synapse_channel import SynapseHub
from synapse_channel.core.release_verification import collect_git_state
from synapse_channel.demo_runtime import (
    _await_listening,
    _run_git,
    _seed_workspace,
    _start_local_hub,
    _validated_hub_receipt,
)


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every default data path of a real hub inside the test's directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


async def _stop(server: asyncio.Task[None]) -> None:
    server.cancel()
    await asyncio.gather(server, return_exceptions=True)


async def test_started_demo_hub_answers_the_listening_probe() -> None:
    """A hub started by the demo runtime completes one clean WebSocket handshake."""
    server, uri = await _start_local_hub(SynapseHub(hub_id="demo-runtime-probe"), 0)
    try:
        port = int(uri.rsplit(":", 1)[1])
        assert port > 0
        await _await_listening(port, timeout=5.0)
        assert not server.done()
    finally:
        await _stop(server)


async def test_start_on_a_taken_port_fails_and_leaves_no_server_running() -> None:
    """A bind failure fails closed before an address is reported; the server task is reaped."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = int(holder.getsockname()[1])
        before = asyncio.all_tasks()
        with pytest.raises(RuntimeError, match="without a bound address"):
            await _start_local_hub(SynapseHub(hub_id="demo-runtime-taken"), taken)
        assert asyncio.all_tasks() == before


async def test_cancelled_start_stops_the_server_it_launched() -> None:
    """Cancelling a start while the hub binds cancels the server task too."""
    before = asyncio.all_tasks()
    starting = asyncio.create_task(_start_local_hub(SynapseHub(hub_id="demo-runtime-cancel"), 0))
    await asyncio.sleep(0)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert asyncio.all_tasks() == before


def test_seed_workspace_is_a_clean_main_branch_repository(tmp_path: Path) -> None:
    """The runtime creates the real Git boundary the mutation guard evaluates."""
    _seed_workspace(tmp_path)

    state = collect_git_state(tmp_path)
    assert _run_git(tmp_path, "branch", "--show-current") == "main"
    assert state.head
    assert state.tree
    assert state.changed_files == []
    assert (tmp_path / "src/shared.py").is_file()
    assert (tmp_path / "tests/test_shared.py").is_file()


def test_git_failure_and_hub_receipt_validation_fail_closed(tmp_path: Path) -> None:
    """Runtime boundary failures remain explicit rather than becoming success."""
    with pytest.raises(RuntimeError, match="not a git repository"):
        _run_git(tmp_path, "status", "--short")
    with pytest.raises(RuntimeError, match="did not return a release receipt"):
        _validated_hub_receipt(None)
    with pytest.raises(RuntimeError, match="unverified evidence"):
        _validated_hub_receipt({"epistemic_status": "unsupported"})
    # The demo runtime accepts ONLY the honest ``unverified`` grade for a
    # caller-supplied hub receipt. A hub that returns ``supported`` for evidence
    # it never verified is fail-closed as an over-claim, not trusted.
    with pytest.raises(RuntimeError, match="unverified evidence"):
        _validated_hub_receipt({"epistemic_status": "supported"})
    unverified = {"epistemic_status": "unverified", "task_id": "T1"}
    assert _validated_hub_receipt(unverified) is unverified
