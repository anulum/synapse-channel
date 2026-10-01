# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real lock and Git hook lifecycle journeys
"""Exercise generated Git hooks inside the public lock command against a real hub."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import secrets
import signal
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run, run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.cli_locking import _lock, _run_subprocess
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


@pytest.mark.asyncio
async def test_refused_scope_extension_preserves_preexisting_owned_claim(tmp_path: Path) -> None:
    """A refused lock renewal cannot release the caller's earlier valid file claim."""
    repo = git_repo(tmp_path / "repository")
    (repo / "other.md").write_text("other owner\n", encoding="utf-8")
    git_run(repo, "add", "other.md")
    git_run(repo, "commit", "-q", "-m", "other scope")
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            for task_id, owner, path in (
                ("existing-lock", "existing-owner", "README.md"),
                ("other-lock", "other-owner", "other.md"),
            ):
                claim = await asyncio.to_thread(
                    run_cli,
                    "git-claim",
                    task_id,
                    "--name",
                    owner,
                    "--paths",
                    path,
                    "--base",
                    "HEAD",
                    "--auto-release-on",
                    "manual",
                    uri=uri,
                    cwd=repo,
                )
                assert claim.ok(), claim.output
            original_epoch = hub.state.claims["existing-lock"].epoch
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "existing-lock",
                "--name",
                "existing-owner",
                "--paths",
                "other.md",
                "--wait-timeout",
                "0",
                "--",
                sys.executable,
                "-c",
                "print('refused-command-ran')",
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == 1, result.output
            assert "Could not acquire lock 'existing-lock'" in result.stdout
            assert "refused-command-ran" not in result.stdout
            retained = hub.state.claims["existing-lock"]
            assert retained.owner == "existing-owner"
            assert retained.paths == ("README.md",)
            assert retained.epoch == original_epoch
            assert hub.state.claims["other-lock"].owner == "other-owner"
            assert not any(row.kind == "release" for row in journal.iter_events())
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_cancellation_after_real_grant_releases_before_command_start(tmp_path: Path) -> None:
    """Cancellation upon an actual grant cleans up the still-connected owner."""
    marker = tmp_path / "command.started"
    journal = EventStore(tmp_path / "hub.db")
    operation: asyncio.Task[int] | None = None

    def observing_agent(
        name: str,
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        **options: Any,
    ) -> SynapseAgent:
        """Observe the real client's grant callback and cancel its owning operation."""

        async def receive(message: dict[str, Any]) -> None:
            """Deliver every actual hub message before interrupting a confirmed grant."""
            await on_message(message)
            if (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == "early-cancel"
                and message.get("owner") == name
            ):
                assert operation is not None
                operation.cancel()

        return SynapseAgent(name, receive, **options)

    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            operation = asyncio.create_task(
                _lock(
                    uri=uri,
                    name="early-owner",
                    task_id="early-cancel",
                    command=[
                        sys.executable,
                        "-c",
                        "import sys; from pathlib import Path; Path(sys.argv[1]).touch()",
                        str(marker),
                    ],
                    paths=[],
                    wait_timeout=0,
                    agent_factory=observing_agent,
                )
            )
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=10)
            assert not marker.exists()
            assert "early-cancel" not in hub.state.claims
            assert any(
                row.kind == "release" and row.payload.get("task_id") == "early-cancel"
                for row in journal.iter_events()
            )
    finally:
        if operation is not None:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_loop,repeat_shutdown", [(False, False), (True, False), (False, True)]
)
async def test_runner_shutdown_during_process_cleanup_does_not_hang(
    tmp_path: Path,
    stop_loop: bool,
    repeat_shutdown: bool,
) -> None:
    """Actual asyncio.run shutdown must finish when it cancels cleanup tasks too."""
    pid_path = tmp_path / "shutdown-child.pid"
    child = (
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    )
    script = (
        "import asyncio,sys\nfrom pathlib import Path\n"
        "from synapse_channel.cli_locking import _lock\n"
        "def interrupt_shutdown(remaining: int) -> None:\n"
        "    for pending in asyncio.all_tasks():\n        pending.cancel()\n"
        "    if remaining:\n"
        "        asyncio.get_running_loop().call_soon(interrupt_shutdown,remaining-1)\n"
        "async def main() -> None:\n"
        "    task=asyncio.create_task(_lock(uri=sys.argv[1],name='shutdown-owner',"
        "task_id='shutdown-mutex',command=[sys.executable,'-c',sys.argv[3],sys.argv[2]],"
        "paths=[],wait_timeout=0))\n"
        "    while not Path(sys.argv[2]).exists():\n"
        "        assert not task.done()\n        await asyncio.sleep(0.02)\n"
        "    task.cancel()\n"
        + (
            "    loop=asyncio.get_running_loop()\n    loop.call_soon(loop.stop)\n"
            if stop_loop
            else "    await asyncio.sleep(0.1)\n"
        )
        + (
            "    asyncio.get_running_loop().call_soon(interrupt_shutdown,32)\n"
            if repeat_shutdown
            else ""
        )
        + "asyncio.run(main())\n"
    )
    async with running_hub() as (hub, uri):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            uri,
            str(pid_path),
            child,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert await asyncio.wait_for(process.wait(), timeout=10) == 0
            assert "shutdown-mutex" not in hub.state.claims
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if pid_path.exists():
                child_pid = int(pid_path.read_text())
                command_line = Path(f"/proc/{child_pid}/cmdline")
                with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                    if (
                        command_line.stat().st_uid == os.getuid()
                        and str(pid_path).encode() in command_line.read_bytes()
                    ):
                        os.killpg(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
@pytest.mark.parametrize("secured", [False, True])
async def test_commit_hook_releases_edit_claim_inside_same_owner_lock(
    tmp_path: Path, secured: bool
) -> None:
    """A successful wrapped commit must durably release its owner's edited-file claim."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    environment = {"SYN_PROJECT": "hook-journey", "SYN_IDENTITY": "hook-journey/owner"}
    token = secrets.token_urlsafe(32) if secured else ""
    environment["SYNAPSE_TOKEN"] = token
    token_path = tmp_path / "hub.token"
    if secured:
        token_path.touch(mode=0o600)
        token_path.write_text(token, encoding="utf-8")
    authenticator = TokenAuthenticator({token: ["hook-journey/owner"]}) if secured else None
    try:
        async with running_hub(SynapseHub(journal=journal, authenticator=authenticator)) as (
            hub,
            uri,
        ):
            for arguments in (
                (
                    "git-init",
                    "--name",
                    "hook-journey/owner",
                    "--base",
                    "HEAD",
                    "--synapse-bin",
                    str(Path(sys.executable).parent / "synapse"),
                    *(["--token-file", str(token_path)] if secured else []),
                ),
                (
                    "git-claim",
                    "edited-file",
                    "--name",
                    "hook-journey/owner",
                    "--base",
                    "HEAD",
                    "--paths",
                    "README.md",
                    "--auto-release-on",
                    "commit",
                ),
            ):
                result = await asyncio.to_thread(
                    run_cli,
                    *arguments,
                    uri=uri,
                    cwd=repo,
                    env=environment,
                )
                assert result.ok(), result.output
            (repo / "README.md").write_text("committed edit\n", encoding="utf-8")
            git_run(repo, "add", "README.md")
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "--name",
                "hook-journey/owner",
                "hook-journey:git",
                "--",
                "git",
                "commit",
                "-m",
                "edited file",
                uri=uri,
                cwd=repo,
                env=environment,
                timeout=30,
            )
            assert result.ok(), result.output
            assert "edited-file" not in hub.state.claims, result.output
            assert "hook-journey:git" not in hub.state.claims, result.output
            assert any(
                row.kind == "release" and row.payload.get("task_id") == "edited-file"
                for row in journal.iter_events()
            ), "commit hook must persist an edit-claim release"
            assert "release requested on commit: edited-file" in result.output
    finally:
        journal.close()
        token_path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_shutdown", [False, True])
async def test_runner_shutdown_retries_cancelled_release_cleanup(
    tmp_path: Path, repeat_shutdown: bool
) -> None:
    """A real server outage and runner shutdown cannot abandon pending socket cleanup."""
    ready = tmp_path / "command.ready"
    stopped = tmp_path / "server.stopped"
    finished = tmp_path / "command.finished"
    child = (
        "import os,sys,time\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while not Path(sys.argv[2]).exists():\n    time.sleep(0.01)\n"
        "Path(sys.argv[3]).write_text('finished')\n"
    )
    script = (
        "import asyncio,sys\nfrom pathlib import Path\n"
        "from synapse_channel.cli_locking import _lock\n"
        "def interrupt_shutdown(remaining: int) -> None:\n"
        "    for pending in asyncio.all_tasks():\n        pending.cancel()\n"
        "    if remaining:\n"
        "        asyncio.get_running_loop().call_soon(interrupt_shutdown,remaining-1)\n"
        "async def main() -> None:\n"
        "    task=asyncio.create_task(_lock(uri=sys.argv[1],name='release-shutdown-owner',"
        "task_id='release-shutdown-mutex',command=[sys.executable,'-c',sys.argv[5],"
        "sys.argv[2],sys.argv[3],sys.argv[4]],paths=[],wait_timeout=0,ready_timeout=0.8))\n"
        "    while not Path(sys.argv[4]).exists():\n"
        "        assert not task.done()\n        await asyncio.sleep(0.01)\n"
        "    await asyncio.sleep(0.15)\n    assert not task.done()\n"
        + (
            "    asyncio.get_running_loop().call_soon(interrupt_shutdown,32)\n"
            if repeat_shutdown
            else ""
        )
        + "asyncio.run(main())\n"
    )
    context = running_hub()
    hub, uri = await context.__aenter__()
    server_closed = False
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        uri,
        str(ready),
        str(stopped),
        str(finished),
        child,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not ready.exists():
            assert process.returncode is None, "runner ended before launching command"
            assert asyncio.get_running_loop().time() < deadline, "command did not start"
            await asyncio.sleep(0.01)
        assert hub.state.claims["release-shutdown-mutex"].owner == "release-shutdown-owner"
        await context.__aexit__(None, None, None)
        server_closed = True
        stopped.write_text("stopped", encoding="utf-8")
        assert await asyncio.wait_for(process.wait(), timeout=5) == 0
        assert finished.read_text() == "finished"
        assert hub.state.claims["release-shutdown-mutex"].owner == "release-shutdown-owner"
        with pytest.raises(ProcessLookupError):
            os.kill(int(ready.read_text()), 0)
    finally:
        # Unblock this test-owned child even if server shutdown/assertion fails.
        stopped.touch()
        if process.returncode is None:
            process.kill()
        await process.wait()
        if not server_closed:
            await context.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ignore_termination,repeat_cancel", [(False, False), (True, False), (True, True)]
)
async def test_cancelled_command_is_reaped_before_mutex_release(
    tmp_path: Path,
    ignore_termination: bool,
    repeat_cancel: bool,
) -> None:
    """Cancellation cannot free the durable claim while its command still runs."""
    pid_path = tmp_path / "child.pid"
    child = (
        "import os,sys,time,signal; from pathlib import Path; "
        + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore_termination else "")
        + "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    )
    async with running_hub() as (hub, uri):
        task = asyncio.create_task(
            _lock(
                uri=uri,
                name="cancel-owner",
                task_id="cancel-mutex",
                command=[sys.executable, "-c", child, str(pid_path)],
                paths=[],
                wait_timeout=0,
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 10
            while not pid_path.exists():
                assert not task.done(), "lock ended before launching its child"
                assert asyncio.get_running_loop().time() < deadline, "child did not start"
                await asyncio.sleep(0.02)
            pid = int(pid_path.read_text())
            assert hub.state.claims["cancel-mutex"].owner == "cancel-owner"
            task.cancel()
            if repeat_cancel:
                await asyncio.sleep(0.1)
                task.cancel()
                await asyncio.sleep(0.2)
                assert not task.done(), "second cancellation bypassed termination grace"
                assert "cancel-mutex" in hub.state.claims
                os.kill(pid, 0)
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            assert "cancel-mutex" not in hub.state.claims
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_teardown", [False, True])
async def test_hub_shutdown_preserves_command_outcome_and_visible_claim(
    caplog: pytest.LogCaptureFixture,
    interrupt_teardown: bool,
) -> None:
    """A real unavailable hub leaves the claim for TTL without changing child exit."""
    hub = SynapseHub()
    context = running_hub(hub)
    _, uri = await context.__aenter__()
    server_closed = False
    closed = asyncio.Event()
    operation: asyncio.Task[int] | None = None

    async def stop_hub_and_run(command: list[str]) -> int:
        """Stop the actual server before executing the wrapped child process."""
        nonlocal server_closed
        await context.__aexit__(None, None, None)
        server_closed = True
        closed.set()
        return await _run_subprocess(command)

    try:
        with caplog.at_level("DEBUG", logger="synapse.lock"):
            operation = asyncio.create_task(
                _lock(
                    uri=uri,
                    name="offline-owner",
                    task_id="offline-mutex",
                    command=[sys.executable, "-c", "raise SystemExit(7)"],
                    paths=[],
                    wait_timeout=0,
                    ready_timeout=0.5,
                    runner=stop_hub_and_run,
                )
            )
            if interrupt_teardown:
                await asyncio.wait_for(closed.wait(), timeout=10)
                await asyncio.sleep(0.1)
                operation.cancel()
                await asyncio.sleep(0.1)
                operation.cancel()
                assert not operation.done(), "interrupt bypassed bounded release cleanup"
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                assert await operation == 7
        assert hub.state.claims["offline-mutex"].owner == "offline-owner"
        assert "could not reconnect to release the held claim" in caplog.text
    finally:
        if operation is not None:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        if not server_closed:
            await context.__aexit__(None, None, None)


@pytest.fixture
def descendant_reaper() -> Iterator[bool]:
    """Adopt this test's orphan on Linux and restore the caller's kernel setting."""
    if sys.platform != "linux":
        yield False
        return
    native = ctypes.CDLL(None, use_errno=True)
    original = ctypes.c_int()
    assert native.prctl(37, ctypes.byref(original), 0, 0, 0) == 0, ctypes.get_errno()
    assert native.prctl(36, 1, 0, 0, 0) == 0, ctypes.get_errno()
    try:
        yield True
    finally:
        assert native.prctl(36, original.value, 0, 0, 0) == 0, ctypes.get_errno()


@pytest.mark.asyncio
async def test_cancellation_stops_descendant_after_group_leader_exits(
    tmp_path: Path, descendant_reaper: bool
) -> None:
    """A TERM-ignoring descendant cannot keep running after its mutex is released."""

    async def process_state(pid: int) -> str:
        """Read the actual POSIX process state, including zombie state, through ps."""
        probe = await asyncio.create_subprocess_exec(
            "ps",
            "-p",
            str(pid),
            "-o",
            "state=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(probe.communicate(), timeout=2)
        finally:
            if probe.returncode is None:
                probe.kill()
            await probe.wait()
        assert probe.returncode in (0, 1), stderr.decode()
        assert not stderr, stderr.decode()
        return stdout.decode().strip()[:1]

    leader_path = tmp_path / "leader.pid"
    descendant_path = tmp_path / "descendant.pid"
    descendant = (
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    )
    leader = (
        "import os,subprocess,sys,time; from pathlib import Path\n"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r},sys.argv[2]])\n"
        "while not Path(sys.argv[2]).exists():\n    time.sleep(0.01)\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(60)\n"
    )
    async with running_hub() as (hub, uri):
        task = asyncio.create_task(
            _lock(
                uri=uri,
                name="tree-owner",
                task_id="tree-mutex",
                command=[sys.executable, "-c", leader, str(leader_path), str(descendant_path)],
                paths=[],
                wait_timeout=0,
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 10
            while not leader_path.exists():
                assert not task.done(), "leader exited before its child registered"
                assert asyncio.get_running_loop().time() < deadline, "leader did not start"
                await asyncio.sleep(0.02)
            descendant_pid = int(descendant_path.read_text())
            assert await process_state(descendant_pid) not in ("", "Z")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            deadline = asyncio.get_running_loop().time() + 5
            while await process_state(descendant_pid) not in ("", "Z"):
                assert asyncio.get_running_loop().time() < deadline, "descendant still executes"
                await asyncio.sleep(0.02)
            assert "tree-mutex" not in hub.state.claims
            if descendant_reaper:
                waited, status = os.waitpid(descendant_pid, os.WNOHANG)
                assert waited == descendant_pid
                assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if descendant_reaper and descendant_path.exists():
                owned_pid = int(descendant_path.read_text())
                try:
                    waited, _status = os.waitpid(owned_pid, os.WNOHANG)
                except ChildProcessError:
                    pass
                else:
                    if not waited:
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(owned_pid, signal.SIGKILL)
                        deadline = asyncio.get_running_loop().time() + 5
                        while not os.waitpid(owned_pid, os.WNOHANG)[0]:
                            assert asyncio.get_running_loop().time() < deadline
                            await asyncio.sleep(0.005)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_disconnected_lock_still_excludes_contender_and_releases(
    tmp_path: Path,
    exit_code: int,
) -> None:
    """A real competing CLI cannot enter while the wrapped process owns its claim."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            child = (
                "import subprocess,sys; "
                "r=subprocess.run([sys.argv[1],'lock','mutex','--name','contender',"
                "'--uri',sys.argv[2],'--wait-timeout','0','--',"
                "sys.executable,'-c','raise SystemExit(99)'],capture_output=True,text=True); "
                "assert r.returncode==1,(r.returncode,r.stdout,r.stderr); "
                "assert 'Could not acquire lock' in r.stdout,r.stdout; "
                "raise SystemExit(int(sys.argv[3]))"
            )
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "owner",
                "--",
                sys.executable,
                "-c",
                child,
                str(Path(sys.executable).parent / "synapse"),
                uri,
                str(exit_code),
                uri=uri,
            )
            assert result.returncode == exit_code, result.output
            assert "mutex" not in hub.state.claims
            events = [row for row in journal.iter_events() if row.payload.get("task_id") == "mutex"]
            assert any(
                row.kind == "claim" and row.payload.get("owner") == "owner" for row in events
            )
            assert any(row.kind == "release" for row in events)
    finally:
        journal.close()
