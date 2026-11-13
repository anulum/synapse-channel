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
import sys
from pathlib import Path

import pytest

from synapse_channel import SynapseAgent, SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_verification import (
    build_verified_release_receipt,
    collect_git_state,
)
from synapse_channel.core.state_models import GitContext
from synapse_channel.demo_runtime import (
    DemoInbox,
    _await_listening,
    _guard,
    _post_story,
    _receipt_fields,
    _release_with_receipt,
    _run_git,
    _seed_workspace,
    _start_local_hub,
    _validated_hub_receipt,
)


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every default data path of a real hub inside the test's directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
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


async def test_inbox_returns_a_later_match_and_times_out_without_one() -> None:
    """The inbox skips noise, waits for a late match, and fails closed on silence."""
    inbox = DemoInbox()
    await inbox({"type": "noise"})
    await inbox({"type": "ready", "n": 1})
    found = await inbox.wait_for(lambda message: message.get("type") == "ready")
    assert found["n"] == 1
    with pytest.raises(TimeoutError, match="expected message did not arrive"):
        await inbox.wait_for(
            lambda message: message.get("type") == "missing",
            start=len(inbox.messages),
            timeout=0.05,
        )

    async def arrive_late() -> None:
        await asyncio.sleep(0.02)
        await inbox({"type": "late"})

    waiter = asyncio.create_task(
        inbox.wait_for(
            lambda message: message.get("type") == "late",
            start=len(inbox.messages),
            timeout=1.0,
        )
    )
    await arrive_late()
    assert (await waiter)["type"] == "late"


async def test_listening_probe_times_out_when_no_hub_answers() -> None:
    """A zero budget and a refused handshake both stop the probe."""
    with pytest.raises(TimeoutError, match="did not start listening"):
        await _await_listening(9, timeout=0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        closed = int(holder.getsockname()[1])
    with pytest.raises(TimeoutError, match="did not start listening"):
        await _await_listening(closed, timeout=0.05)


def test_receipt_fields_keep_absent_evidence_empty() -> None:
    """A sparse hub receipt becomes the keyword fields a release accepts."""
    empty = _receipt_fields({})
    assert empty["evidence"] == []
    assert empty["confidence"] == ""
    assert empty["freshness_seconds"] is None
    filled = _receipt_fields(
        {
            "evidence": ["seen"],
            "artifacts": ["a"],
            "known_failures": [],
            "changed_files": ["src/shared.py"],
            "generated_artifacts": [],
            "approvals": [],
            "confidence": "observed",
            "freshness_seconds": 1.5,
        }
    )
    assert filled["evidence"] == ["seen"]
    assert filled["changed_files"] == ["src/shared.py"]
    assert filled["freshness_seconds"] == 1.5


async def test_live_hub_records_a_milestone_denies_an_unclaimed_edit_and_grades_release(
    tmp_path: Path,
) -> None:
    """Progress, the mutation guard, and release all talk to the demo hub."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _seed_workspace(workspace)
    server, uri = await _start_local_hub(SynapseHub(hub_id="demo-runtime-story"), 0)
    inbox = DemoInbox()
    agent = SynapseAgent("DEMO", inbox, uri=uri, verbose=False, machine_identity=False)
    connection = asyncio.create_task(agent.connect())
    try:
        await agent.wait_until_ready(3.0)
        await agent.post_task("TASK", title="Record the handoff")
        await _post_story(agent, inbox, task_id="TASK", text="milestone recorded")
        assert any(
            message.get("note", {}).get("text") == "milestone recorded"
            for message in inbox.messages
        )
        verdict = await _guard(
            workspace,
            Path("src/shared.py"),
            "call-1",
            provider="Codex CLI",
            identity="DEMO",
            uri=uri,
        )
        assert verdict.allowed is False
        git_context = GitContext(branch="main", base="main", auto_release_on="manual").as_dict()
        await agent.claim(
            "TASK",
            worktree=str(workspace.resolve()),
            paths=["src/shared.py"],
            git=git_context,
        )
        await inbox.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == "TASK"
            )
        )
        state = collect_git_state(workspace)
        receipt = build_verified_release_receipt(
            task_id="TASK",
            owner="DEMO",
            commands=[[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]],
            artifacts=[workspace / "src/shared.py"],
            changed_files=state.changed_files,
            git_head=state.head,
            git_tree=state.tree,
            cwd=workspace,
            command_timeout_seconds=30.0,
        )
        assert receipt["known_failures"] == []
        graded = await _release_with_receipt(agent, inbox, "TASK", receipt)
        assert graded["epistemic_status"] == "unverified"
        assert graded.get("task_id") == "TASK"
    finally:
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        await _stop(server)
