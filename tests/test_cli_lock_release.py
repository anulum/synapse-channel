# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real lock teardown and exact recovery journeys
"""Exercise fresh epoch lookup and one-shot release through real CLI sockets."""

from __future__ import annotations

import asyncio
import shlex
import socket
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from cli_lock_release_helpers import release_reply_proxy
from hub_e2e_helpers import running_hub
from synapse_channel.cli_locking import _lock
from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

_RENEW = """
import asyncio,sys
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
async def main():
    granted=asyncio.Event()
    async def receive(frame):
        if frame.get('type')==MessageType.CLAIM_GRANTED and frame.get('task_id')=='mutex':
            granted.set()
    agent=SynapseAgent('mutex-owner',receive,uri=sys.argv[1],verbose=False)
    listener=asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(3)
        await agent.claim('mutex',worktree='mutex')
        await asyncio.wait_for(granted.wait(),3)
    finally:
        agent.running=False
        listener.cancel()
        await asyncio.gather(listener,return_exceptions=True)
asyncio.run(main())
"""

_CHANGE_EPOCH_CACHE = """
import pathlib,sys
root=pathlib.Path(sys.argv[1])/'synapse'/'lease-epoch'
files=[p for p in root.rglob('*') if p.is_file()]
assert len(files)==1
path=files[0]
if sys.argv[2]=='absent':
    path.unlink()
else:
    path.write_text('invalid epoch',encoding='ascii')
"""


@pytest.mark.asyncio
async def test_interrupted_cleanup_recovers_the_original_dispatch_without_replay(
    tmp_path: Path,
) -> None:
    """Cancel the actual teardown after dispatch and settle it through a read-only query."""
    journal = EventStore(tmp_path / "hub.db")
    operation: asyncio.Task[int] | None = None
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            async with release_reply_proxy(uri, drop_confirmation=True) as (proxy, fault):
                operation = asyncio.create_task(
                    _lock(
                        uri=proxy,
                        name="interrupted-owner",
                        task_id="interrupted-mutex",
                        command=[sys.executable, "-c", "pass"],
                        paths=[],
                        wait_timeout=0,
                    )
                )
                deadline = asyncio.get_running_loop().time() + 10
                while not fault["release_seen"]:
                    assert not operation.done()
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                assert fault["release_dispatches"] == 1
                cleanup = [
                    task
                    for task in asyncio.all_tasks()
                    if getattr(task.get_coro(), "__qualname__", "") == "_lock.<locals>.teardown"
                ]
                assert len(cleanup) == 1
                cleanup[0].cancel()
                fault["active"] = False
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 10)
                assert fault["release_dispatches"] == 1
                assert fault["confirmation_queries"] == 1
                assert not hub.state.claims
                releases = [row for row in journal.iter_events() if row.kind == "release"]
                assert len(releases) == 1
    finally:
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["absent", "malformed"])
async def test_original_grant_survives_loss_of_the_local_epoch_cache(
    tmp_path: Path, cache_state: str
) -> None:
    """An actual child cache loss falls back to the observed grant on a fenced hub."""
    data_home = tmp_path / "data-home"
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            hub,
            uri,
        ):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "mutex-owner",
                "--",
                sys.executable,
                "-c",
                _CHANGE_EPOCH_CACHE,
                str(data_home),
                cache_state,
                uri=uri,
                env={"XDG_DATA_HOME": str(data_home)},
            )
            assert result.returncode == 0, result.output
            assert not result.stderr
            assert "mutex" not in hub.state.claims
            events = list(journal.iter_events())
            assert len([row for row in events if row.kind == "claim"]) == 1
            assert len([row for row in events if row.kind == "release"]) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_explicit_file_scope_outside_git_keeps_the_shared_namespace(
    tmp_path: Path,
) -> None:
    """Respect real Git markers while preserving the genuine non-Git namespace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "shared.txt").write_text("shared", encoding="utf-8")
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "shared-mutex",
                "--name",
                "mutex-owner",
                "--paths",
                "shared.txt",
                "--",
                sys.executable,
                "-c",
                "pass",
                uri=uri,
                cwd=workspace,
            )
            enclosing_git = any((parent / ".git").exists() for parent in workspace.parents)
            assert result.returncode == (1 if enclosing_git else 0), result.output
            assert not hub.state.claims
            claims = [row for row in journal.iter_events() if row.kind == "claim"]
            if enclosing_git:
                assert "could not resolve the current Git worktree" in result.output
                assert not claims
                return
            assert len(claims) == 1
            assert claims[0].payload["worktree"] == ""
            assert claims[0].payload["paths"] == ["shared.txt"]
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.real_hub
async def test_child_renewal_releases_the_current_durable_epoch(tmp_path: Path) -> None:
    """A same-identity child grant cannot leave its renewed mutex after outer exit."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            hub,
            uri,
        ):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "mutex-owner",
                "--",
                sys.executable,
                "-c",
                _RENEW,
                uri,
                uri=uri,
            )
            assert result.returncode == 0, result.output
            assert not result.stderr
            assert "mutex" not in hub.state.claims
            events = list(journal.iter_events())
            claims = [event for event in events if event.kind == "claim"]
            assert len(claims) == 2
            assert claims[1].payload["epoch"] > claims[0].payload["epoch"]
            assert len([event for event in events if event.kind == "release"]) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_other_task_owner_hub_and_operation_replies_are_not_confirmation(
    tmp_path: Path,
) -> None:
    """Foreign frames on a real connection cannot confirm this actual keyed release."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            async with release_reply_proxy(
                uri,
                drop_confirmation=False,
                inject_foreign=True,
            ) as (proxy, fault):
                fault["active"] = False
                result = await asyncio.to_thread(
                    run_cli,
                    "lock",
                    "mutex",
                    "--name",
                    "mutex-owner",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                    uri=proxy,
                )
                assert result.returncode == 0, result.output
                assert not result.stderr
                assert not hub.state.claims
                events = list(journal.iter_events())
                assert len([row for row in events if row.kind == "claim"]) == 1
                assert len([row for row in events if row.kind == "release"]) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.real_hub
@pytest.mark.parametrize("child_exit", [0, 7])
async def test_refused_cleanup_is_visible_without_losing_child_failure(
    tmp_path: Path,
    child_exit: int,
) -> None:
    """Actual ACL refusal is nonzero after child success and preserves child failure."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        policy = AclPolicy([AclRule(CLAIM, "claim", "*")])
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "mutex-owner",
                "--",
                sys.executable,
                "-c",
                f"raise SystemExit({child_exit})",
                uri=uri,
            )
            assert result.returncode == (1 if child_exit == 0 else child_exit)
            assert "lock: release refused" in result.stderr
            assert f"child exit={child_exit}" in result.stderr
            assert hub.state.claims["mutex"].owner == "mutex-owner"
            assert not any(event.kind == "release" for event in journal.iter_events())
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.real_hub
@pytest.mark.parametrize("drop_confirmation", [False, True])
@pytest.mark.parametrize("private_uri", [False, True])
async def test_lost_reply_recovers_only_the_original_durable_operation(
    tmp_path: Path,
    drop_confirmation: bool,
    private_uri: bool,
) -> None:
    """Loss either recovers through an exact read or exposes a reusable read-only key."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            async with release_reply_proxy(uri, drop_confirmation=drop_confirmation) as (
                proxy,
                fault,
            ):
                requested_uri = proxy + "/bus?token=fixture-query-private" if private_uri else proxy
                result = await asyncio.to_thread(
                    run_cli,
                    "lock",
                    "mutex",
                    "--name",
                    "mutex-owner",
                    "--release-timeout",
                    "0.1",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                    uri=requested_uri,
                )
                assert result.returncode == (3 if drop_confirmation else 0), result.output
                assert "mutex" not in hub.state.claims
                before = [event.seq for event in journal.iter_events() if event.kind == "release"]
                assert len(before) == 1
                if drop_confirmation:
                    assert "child exit=0" in result.stderr
                    assert "Do not replay release" in result.stderr
                    assert "fixture-query-private" not in result.stderr
                    if private_uri:
                        assert "Restore the original hub URI privately" in result.stderr
                    recovery = result.stderr.split("Read-only recovery: ", 1)[1].strip()
                    arguments = shlex.split(recovery)[1:]
                    assert "--confirm-only" in arguments
                    fault["active"] = False
                    readback = await asyncio.to_thread(
                        run_cli, *arguments, env={"SYNAPSE_URI": requested_uri}
                    )
                    assert readback.returncode == 0, readback.output
                else:
                    assert not result.stderr
                after = [event.seq for event in journal.iter_events() if event.kind == "release"]
                assert after == before
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.real_hub
async def test_absent_claim_without_durable_confirmation_is_unknown() -> None:
    """Successful mutation on a volatile hub cannot manufacture a durable receipt."""
    async with running_hub() as (hub, uri):
        async with release_reply_proxy(uri, drop_confirmation=False) as (proxy, _fault):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "mutex-owner",
                "--release-timeout",
                "0.1",
                "--",
                sys.executable,
                "-c",
                "pass",
                uri=proxy,
            )
            assert result.returncode == 3, result.output
            assert "mutex" not in hub.state.claims
            assert "lock: release unknown" in result.stderr


@pytest.mark.asyncio
async def test_private_uri_is_not_exposed_when_acquisition_fails() -> None:
    """A real unavailable listener never exposes URI credentials in lock diagnostics."""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        uri = f"ws://fixture-user:fixture-password@127.0.0.1:{port}/bus?token=fixture-query"
        result = await asyncio.to_thread(
            run_cli,
            "lock",
            "mutex",
            "--name",
            "mutex-owner",
            "--ready-timeout",
            "0.1",
            "--",
            sys.executable,
            "-c",
            "print('must-not-run')",
            uri=uri,
        )
        assert result.returncode == 1, result.output
        assert "<configured hub>" in result.output
        assert "fixture-password" not in result.output
        assert "fixture-query" not in result.output
        assert "must-not-run" not in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1", "301"])
async def test_invalid_release_deadline_cannot_acquire_or_execute(
    tmp_path: Path,
    timeout: str,
) -> None:
    """Invalid deadlines fail before acquiring a mutex or running its child."""
    marker = tmp_path / "ran"
    async with running_hub() as (hub, uri):
        result = await asyncio.to_thread(
            run_cli,
            "lock",
            "mutex",
            "--name",
            "mutex-owner",
            f"--release-timeout={timeout}",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
            uri=uri,
        )
        assert result.returncode == 2, result.output
        assert "timeouts must be finite" in result.stderr
        assert not marker.exists()
        assert not hub.state.claims
